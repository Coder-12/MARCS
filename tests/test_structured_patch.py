import hashlib
import os
import stat

import pytest

from orchestration.contracts import CreateFileChange, ModifyFileChange, PatchProposal
from services import structured_patch as module
from services.structured_patch import apply_structured_patch
from services.workspace import Workspace, create_workspace, dispose_workspace


def snapshot(root):
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


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_bytes(b"alpha\r\n")
    (source / "a.py").chmod(0o755)
    (source / "b.py").write_bytes(b"beta\n")
    before = snapshot(source)
    workspace = create_workspace(source)
    assert snapshot(source) == before
    try:
        yield source, workspace
    finally:
        assert snapshot(source) == before
        dispose_workspace(workspace)
        assert snapshot(source) == before


def modify(workspace, path="a.py", snippet="alpha", replacement="changed", **overrides):
    data = {"path": path, "original_sha256": hashlib.sha256((workspace.workspace_root / path).read_bytes()).hexdigest(),
            "original_snippet": snippet, "replacement_snippet": replacement, "justification": "Test change"}
    return ModifyFileChange(**{**data, **overrides})


def create(path="new/file.py", content="new\n"):
    return CreateFileChange(path=path, content=content, justification="Test creation")


def proposal(*changes):
    return PatchProposal(patch_id="patch-1", run_id="run-1", iteration=1, files=list(changes))


def rejected(source, workspace, patch, match=None):
    source_before, before = snapshot(source), snapshot(workspace.workspace_root)
    result = apply_structured_patch(workspace, patch)
    assert result.status == "validation_failed"
    assert result.error and result.error.strip()
    if match:
        assert match in result.error
    assert result.files_modified == [] and result.unified_diff == ""
    assert snapshot(workspace.workspace_root) == before
    assert snapshot(source) == source_before
    return result


def test_modify_and_create_success_preserves_raw_bytes_modes_and_source(candidate):
    source, workspace = candidate
    patch = proposal(create("z/new.py", "created\n"), modify(workspace))
    result = apply_structured_patch(workspace, patch)
    assert result.status == "success" and result.error is None and result.patch_id == "patch-1"
    assert result.files_modified == ["a.py", "z/new.py"]
    assert (workspace.workspace_root / "a.py").read_bytes() == b"changed\r\n"
    assert stat.S_IMODE((workspace.workspace_root / "a.py").stat().st_mode) == 0o755
    assert (workspace.workspace_root / "z/new.py").read_bytes() == b"created\n"
    assert "--- a/a.py\n+++ b/a.py\n" in result.unified_diff
    assert "-alpha\r\n+changed\r\n" in result.unified_diff
    assert "--- /dev/null\n+++ b/z/new.py\n" in result.unified_diff
    assert result.unified_diff.index("b/a.py") < result.unified_diff.index("b/z/new.py")
    assert not list(workspace.workspace_root.rglob(".marcs-write-*"))
    assert not (source / "z").exists()


@pytest.mark.parametrize("content", ["", "no final newline", "café\n"])
def test_create_including_empty_and_utf8_is_real_change(candidate, content):
    source, workspace = candidate
    result = apply_structured_patch(workspace, proposal(create(content=content)))
    assert result.status == "success" and result.files_modified == ["new/file.py"]
    assert (workspace.workspace_root / "new/file.py").read_bytes() == content.encode("utf-8")
    assert "--- /dev/null\n+++ b/new/file.py\n" in result.unified_diff
    assert not (source / "new").exists()
    if not content:
        assert "@@ -0,0 +0,0 @@" in result.unified_diff
    elif not content.endswith("\n"):
        assert "\\ No newline at end of file" in result.unified_diff


def test_complete_diff_is_deterministic_and_scoped(candidate):
    source, workspace = candidate
    (workspace.workspace_root / "unrelated.txt").write_text("must not enter diff")
    contents = "".join(f"line-{i:05d}-{'x' * 60}\n" for i in range(2500)) + "FINAL_SENTINEL\n"
    patch = proposal(create("large.txt", contents), modify(workspace))
    first = apply_structured_patch(workspace, patch)
    with create_workspace(source) as second_workspace:
        second = apply_structured_patch(second_workspace, patch)
    assert first == second
    assert len(first.unified_diff) > 100000 and "+FINAL_SENTINEL\n" in first.unified_diff
    assert "TRUNCATED" not in first.unified_diff and "unrelated.txt" not in first.unified_diff


@pytest.mark.parametrize("problem", ["hash", "absent", "repeated", "overlapping", "noop", "utf8", "normalized_hash"])
def test_exact_modify_validation_rejects_without_writes(candidate, problem):
    source, workspace = candidate
    target = workspace.workspace_root / "a.py"
    if problem == "hash":
        change = modify(workspace, original_sha256="0" * 64)
    elif problem == "absent":
        change = modify(workspace, snippet="absent")
    elif problem == "repeated":
        target.write_bytes(b"alpha alpha")
        change = modify(workspace)
    elif problem == "overlapping":
        target.write_bytes(b"aaa")
        change = modify(workspace, snippet="aa")
    elif problem == "noop":
        change = modify(workspace, replacement="alpha")
    elif problem == "utf8":
        target.write_bytes(b"\xffalpha")
        change = modify(workspace)
    else:
        normalized = hashlib.sha256(b"alpha\n").hexdigest()
        change = modify(workspace, original_sha256=normalized)
    rejected(source, workspace, proposal(change))


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_modify_requires_regular_existing_file(candidate, kind):
    source, workspace = candidate
    path = "absent" if kind == "missing" else "directory"
    if kind == "directory":
        (workspace.workspace_root / path).mkdir()
    change = ModifyFileChange(path=path, original_sha256="0" * 64, original_snippet="x",
                              replacement_snippet="y", justification="Test")
    rejected(source, workspace, proposal(change), "existing regular file")


def test_special_candidate_target_is_rejected_without_reading(candidate):
    if not hasattr(os, "mkfifo"):
        pytest.skip("OS has no named-pipe fixture support")
    source, workspace = candidate
    os.mkfifo(workspace.workspace_root / "pipe")
    change = ModifyFileChange(path="pipe", original_sha256="0" * 64, original_snippet="x",
                              replacement_snippet="y", justification="Test")
    rejected(source, workspace, proposal(change), "existing regular file")


@pytest.mark.parametrize("path", ["a.py", "a.py/new.py", "existing-dir"])
def test_create_rejects_occupied_target_or_file_parent(candidate, path):
    source, workspace = candidate
    (workspace.workspace_root / "existing-dir").mkdir()
    rejected(source, workspace, proposal(create(path)))


@pytest.mark.parametrize("path", [
    ".env", "config/.ENV", "config/secrets.prod", "config/SECRETS.prod", "cert/private.key",
    "cert/PRIVATE.KEY", "cert/server.pem", "cert/SERVER.PEM", ".git/config", "nested/.GIT/index",
])
def test_blocked_targets_fail_without_parent_creation(candidate, path):
    source, workspace = candidate
    rejected(source, workspace, proposal(create(path)), "blocked target")


@pytest.mark.parametrize("path", [".env.example", "my.pem.backup", "monkey.py", "git_notes.txt"])
def test_block_policy_has_no_substring_false_positives(candidate, path):
    source, workspace = candidate
    result = apply_structured_patch(workspace, proposal(create(path)))
    assert result.status == "success" and result.files_modified == [path]
    assert not (source / path).exists()


@pytest.mark.parametrize("kind", ["leaf_outside", "parent_outside", "leaf_inside", "parent_inside", "broken_leaf", "broken_parent"])
def test_symlink_targets_and_parents_never_receive_writes(candidate, tmp_path, kind):
    source, workspace = candidate
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "file.py").write_bytes(b"external")
    external_before = snapshot(outside)
    root = workspace.workspace_root
    if kind.endswith("inside"):
        destination = root / "b.py" if kind.startswith("leaf") else root
    elif kind.startswith("broken"):
        destination = tmp_path / "does-not-exist"
    else:
        destination = outside / "file.py" if kind.startswith("leaf") else outside
    link = root / "linked"
    link.symlink_to(destination, target_is_directory="parent" in kind)
    path = "linked/file.py" if "parent" in kind else "linked"
    change = create(path) if kind.startswith("broken") else ModifyFileChange(
        path=path, original_sha256="0" * 64, original_snippet="external", replacement_snippet="changed",
        justification="Test rejection")
    rejected(source, workspace, proposal(change), "symlink")
    assert snapshot(outside) == external_before


def test_whole_proposal_validation_creates_neither_files_nor_directories(candidate):
    source, workspace = candidate
    patch = proposal(create("a-new/child.py"), modify(workspace),
                     modify(workspace, "b.py", "beta", original_sha256="0" * 64))
    rejected(source, workspace, patch, "SHA-256 mismatch")
    assert not (workspace.workspace_root / "a-new").exists()


@pytest.mark.parametrize("paths", [("pkg", "pkg/new.py"), ("A.py", "a.py"), ("Pkg", "pkg/new.py")])
def test_conflicting_or_case_ambiguous_target_graph_is_rejected(candidate, paths):
    source, workspace = candidate
    rejected(source, workspace, proposal(*(create(path) for path in paths)))


@pytest.mark.parametrize("path", ["/escape.py", "../escape.py", "pkg/../../escape.py", "a//b", "C:/escape.py", "name:stream", "pkg./x"])
def test_model_construct_bypass_cannot_escape_or_create_ambiguous_targets(candidate, path):
    source, workspace = candidate
    bad = CreateFileChange.model_construct(path=path, content="bad", justification="Test")
    patch = PatchProposal.model_construct(patch_id="patch-1", run_id="run-1", iteration=1, files=[bad])
    rejected(source, workspace, patch)


@pytest.mark.parametrize("path", ["../escape.py", "/escape.py", ".git/config"])
def test_filesystem_defense_is_independent_of_schema_validation(candidate, monkeypatch, path):
    source, workspace = candidate
    bad = CreateFileChange.model_construct(path=path, content="bad", justification="Test")
    patch = PatchProposal.model_construct(patch_id="patch-1", run_id="run-1", iteration=1, files=[bad])
    monkeypatch.setattr(PatchProposal, "model_validate", classmethod(lambda cls, value: value))
    rejected(source, workspace, patch)


def test_mutated_duplicate_files_and_unattributable_schema_failure(candidate):
    source, workspace = candidate
    patch = proposal(create())
    patch.files.append(patch.files[0])
    rejected(source, workspace, patch, "schema invalid")
    malformed = PatchProposal.model_construct(patch_id="", run_id="run-1", iteration=1, files=[])
    result = rejected(source, workspace, malformed, "schema invalid")
    assert result.patch_id is None


@pytest.mark.parametrize("kind", ["disposed", "removed", "invalid", "uninitialized"])
def test_nonlive_or_invalid_workspace_cannot_be_patched(candidate, kind):
    source, workspace = candidate
    patch = proposal(create())
    before = snapshot(source)
    if kind == "disposed":
        dispose_workspace(workspace)
    elif kind == "removed":
        import shutil
        shutil.rmtree(workspace.workspace_root)
    elif kind == "invalid":
        workspace = source
    else:
        workspace = Workspace.__new__(Workspace)
    result = apply_structured_patch(workspace, patch)
    assert result.status == "validation_failed" and result.error
    assert result.files_modified == [] and result.unified_diff == ""
    assert snapshot(source) == before


def test_workspace_subclass_cannot_redirect_patch_writes(candidate):
    source, workspace = candidate
    before = snapshot(source)

    class RedirectedWorkspace(Workspace):
        def _require_live(self):
            return source

    redirected = RedirectedWorkspace(source)
    try:
        result = apply_structured_patch(redirected, proposal(create("escape.py")))
        assert result.status == "validation_failed"
        assert snapshot(source) == before
    finally:
        Workspace.dispose(redirected)


def test_apply_failure_after_first_write_leaves_disposable_partial_candidate(candidate, monkeypatch):
    source, workspace = candidate
    source_before = snapshot(source)
    original_writer = module._write_change
    calls = []

    def fail_second(change):
        calls.append(change.path)
        if len(calls) == 2:
            raise OSError("injected write failure")
        original_writer(change)

    monkeypatch.setattr(module, "_write_change", fail_second)
    result = apply_structured_patch(workspace, proposal(modify(workspace), modify(workspace, "b.py", "beta")))
    assert calls == ["a.py", "b.py"]
    assert result.status == "apply_failed" and "injected write failure" in result.error
    assert result.unified_diff == "" and result.files_modified == ["a.py"]
    assert (workspace.workspace_root / "a.py").read_bytes() == b"changed\r\n"
    assert (workspace.workspace_root / "b.py").read_bytes() == b"beta\n"
    assert snapshot(source) == source_before
    owned = workspace.workspace_root.parent
    dispose_workspace(workspace)
    assert not owned.exists() and snapshot(source) == source_before


def test_failed_atomic_replace_keeps_existing_mode_and_cleans_staging_file(candidate, monkeypatch):
    source, workspace = candidate
    before = snapshot(workspace.workspace_root)

    def fail(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr(module.os, "replace", fail)
    result = apply_structured_patch(workspace, proposal(modify(workspace)))
    assert result.status == "apply_failed" and result.error and not result.unified_diff
    assert snapshot(workspace.workspace_root) == before
    assert not list(workspace.workspace_root.glob(".marcs-write-*"))
