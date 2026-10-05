import os
from pathlib import Path
import socket
import stat
import tempfile

import pytest

from services import workspace as module
from services.workspace import Workspace, WorkspaceError, create_workspace, dispose_workspace


def snapshot(root):
    """Names, bytes, mode bits and link texts; never follow fixture symlinks."""
    result = {}
    for entry in sorted(root.iterdir()):
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            result[entry.name] = ("link", os.readlink(entry))
        elif stat.S_ISDIR(mode):
            result[entry.name] = ("dir", stat.S_IMODE(mode), snapshot(entry))
        elif stat.S_ISREG(mode):
            result[entry.name] = ("file", stat.S_IMODE(mode), entry.read_bytes())
        else:
            result[entry.name] = ("special", stat.S_IFMT(mode))
    return result


@pytest.mark.parametrize("git_kind", ["directory", "file", "symlink"])
def test_copy_excludes_nested_git_metadata_preserves_links_and_source(tmp_path, git_kind):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"not candidate-owned")
    (source / "app.py").write_bytes(b"print('copied')\r\n")
    (source / "app.py").chmod(0o755)
    (source / ".gitignore").write_text("ignored\n")
    (source / ".gitattributes").write_text("*.py text\n")
    (source / ".github").mkdir()
    (source / ".github/check.yml").write_text("name: checks\n")
    (source / "linked-data").symlink_to(outside, target_is_directory=True)
    (source / "broken").symlink_to(tmp_path / "absent")
    nested = source / "submodule"
    nested.mkdir()
    for parent in (source, nested):
        metadata = parent / ".git"
        if git_kind == "directory":
            metadata.mkdir()
            (metadata / "config").write_text("metadata")
        elif git_kind == "file":
            metadata.write_text("gitdir: outside")
        else:
            metadata.symlink_to(outside, target_is_directory=True)
    before, external_before = snapshot(source), snapshot(outside)
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    with create_workspace(alias) as candidate:
        root = candidate.workspace_root
        assert candidate.source_root == source.resolve()
        assert not root.is_relative_to(source.resolve())
        assert root.is_dir() and root.parent.is_dir()
        assert (root / "app.py").read_bytes() == b"print('copied')\r\n"
        assert stat.S_IMODE((root / "app.py").stat().st_mode) == 0o755
        assert (root / ".gitignore").read_text() == "ignored\n"
        assert (root / ".gitattributes").is_file() and (root / ".github/check.yml").is_file()
        assert not os.path.lexists(root / ".git")
        assert not os.path.lexists(root / "submodule/.git")
        assert (root / "linked-data").is_symlink()
        assert os.readlink(root / "linked-data") == str(outside)
        assert (root / "broken").is_symlink()
        assert snapshot(source) == before and snapshot(outside) == external_before
        owned = root.parent
    assert not owned.exists()
    assert snapshot(source) == before and snapshot(outside) == external_before


def test_fresh_candidates_are_independent_and_disposal_is_idempotent(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_bytes(b"original")
    before = snapshot(source)
    first, second = create_workspace(source), create_workspace(source)
    try:
        assert first.workspace_root != second.workspace_root
        (first.workspace_root / "a.py").write_bytes(b"candidate")
        assert (second.workspace_root / "a.py").read_bytes() == b"original"
        owned = first.workspace_root.parent
        dispose_workspace(first)
        dispose_workspace(first)
        assert not owned.exists() and second.workspace_root.is_dir()
        with pytest.raises(WorkspaceError, match="not live"):
            first._require_live()
    finally:
        dispose_workspace(first)
        dispose_workspace(second)
    assert source.is_dir() and snapshot(source) == before


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_source_is_rejected(tmp_path, kind):
    source = tmp_path / "source"
    if kind == "file":
        source.write_bytes(b"keep")
    with pytest.raises(WorkspaceError):
        create_workspace(source)
    assert source.read_bytes() == b"keep" if kind == "file" else not source.exists()


@pytest.mark.parametrize("kind", ["fifo", "socket"])
def test_special_source_entries_rejected_without_reading(tmp_path, monkeypatch, kind):
    source, storage = tmp_path / "source", tmp_path / "storage"
    source.mkdir()
    storage.mkdir()
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(storage))
    special = source / "special"
    server = None
    if kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("OS has no named-pipe fixture support")
        os.mkfifo(special)
    else:
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("OS has no Unix-socket fixture support")
        server = socket.socket(socket.AF_UNIX)
        # Unix sockets have a short pathname limit; pytest's temp root can exceed it.
        with tempfile.TemporaryDirectory(prefix="marcs-sock-", dir="/tmp") as short:
            address = Path(short) / "socket"
            server.bind(str(address))
            address.rename(special)
    before = snapshot(source)

    def forbidden_read(*args, **kwargs):
        pytest.fail("unsupported object must be rejected before copy/read")

    monkeypatch.setattr(module.shutil, "copy2", forbidden_read)
    try:
        with pytest.raises(WorkspaceError, match="unsupported source"):
            create_workspace(source)
        assert list(storage.iterdir()) == []
        assert snapshot(source) == before
    finally:
        if server is not None:
            server.close()


def test_failed_copy_cleans_owned_partial_tree(tmp_path, monkeypatch):
    source, storage = tmp_path / "source", tmp_path / "storage"
    source.mkdir()
    storage.mkdir()
    (source / "a").write_bytes(b"keep")
    before = snapshot(source)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(storage))

    def partial_failure(src, dst, **kwargs):
        Path(dst).mkdir()
        (Path(dst) / "partial").write_bytes(b"partial copy")
        raise OSError("injected copy failure")

    monkeypatch.setattr(module.shutil, "copytree", partial_failure)
    with pytest.raises(WorkspaceError, match="copy failed"):
        create_workspace(source)
    assert list(storage.iterdir()) == [] and snapshot(source) == before


def test_temp_storage_inside_source_is_rejected_before_creation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    storage = source / "temp"
    storage.mkdir()
    before = snapshot(source)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(storage))
    with pytest.raises(WorkspaceError, match="outside the source"):
        create_workspace(source)
    assert snapshot(source) == before


def test_cleanup_ownership_cannot_be_supplied_or_redirected(tmp_path):
    source, unrelated = tmp_path / "source", tmp_path / "unrelated"
    source.mkdir()
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"keep")
    with pytest.raises(TypeError):
        Workspace(workspace_root=unrelated)
    with pytest.raises(WorkspaceError):
        dispose_workspace(unrelated)
    with pytest.raises(WorkspaceError):
        dispose_workspace(Workspace.__new__(Workspace))
    with create_workspace(source) as candidate:
        with pytest.raises(AttributeError):
            candidate.workspace_root = unrelated
        with pytest.raises(AttributeError):
            candidate.source_root = unrelated
    assert (unrelated / "keep").read_bytes() == b"keep" and source.exists()


def test_disposal_does_not_dispatch_to_unowned_subclass_cleanup():
    class RedirectedWorkspace(Workspace):
        def dispose(self):
            pytest.fail("cleanup must not dispatch to a caller-supplied implementation")

    with pytest.raises(WorkspaceError):
        dispose_workspace(RedirectedWorkspace.__new__(RedirectedWorkspace))


def test_replaced_candidate_link_is_not_live_and_cleanup_does_not_follow_it(tmp_path):
    source, unrelated = tmp_path / "source", tmp_path / "unrelated"
    source.mkdir()
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"keep")
    candidate = create_workspace(source)
    root = candidate.workspace_root
    root.rmdir()
    root.symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="not live"):
        candidate._require_live()
    dispose_workspace(candidate)
    assert (unrelated / "keep").read_bytes() == b"keep" and source.exists()
