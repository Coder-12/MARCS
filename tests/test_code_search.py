import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

from services import code_search as module
from services.code_search import RepositorySearchError, search_code
from services.repository import RepositoryError, RepositoryView


def snapshot(root):
    result = {}
    for path in sorted(root.iterdir()):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            result[path.name] = ("link", os.readlink(path))
        elif stat.S_ISDIR(mode):
            result[path.name] = ("dir", stat.S_IMODE(mode), snapshot(path))
        elif stat.S_ISREG(mode):
            result[path.name] = ("file", stat.S_IMODE(mode), path.read_bytes())
        else:
            result[path.name] = ("special", stat.S_IFMT(mode))
    return result


def populate(root, files):
    root.mkdir(exist_ok=True)
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return root


@contextmanager
def unchanged(root):
    before = snapshot(root)
    try:
        yield RepositoryView(root)
    finally:
        assert snapshot(root) == before


@pytest.mark.parametrize("query", ["", " \t", "a\x00b", "a\nb", "a\rb", "x" * 9, "\ud800"])
def test_invalid_queries_fail_before_process_or_content_reads(tmp_path, monkeypatch, query):
    source = populate(tmp_path / "source", {"a.py": "source"})
    with unchanged(source) as view:
        with monkeypatch.context() as guard:
            guard.setattr(Path, "open", lambda *a, **kw: pytest.fail("invalid query must not read content"))
            guard.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("invalid query must not launch"))
            with pytest.raises(RepositorySearchError, match="invalid_query"):
                search_code(view, query, max_query_bytes=8)


@pytest.mark.parametrize("backend", ["python", "ripgrep"])
@pytest.mark.parametrize("case_sensitive", [False, True])
def test_literal_case_and_option_text_have_equal_canonical_semantics(tmp_path, backend, case_sensitive):
    if backend == "ripgrep" and shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed in validation PATH")
    source = populate(tmp_path / "source", {"odd:name.py": "Needle\nneedle\n--hidden\n.*[literal]\ncafé\nCAFÉ\n", ".hidden.py": "needle\n"})
    with unchanged(source) as view:
        result = search_code(view, "needle", backend=backend, case_sensitive=case_sensitive)
        assert result.backend == backend
        expected = [(".hidden.py", 1), ("odd:name.py", 2)] if case_sensitive else [(".hidden.py", 1), ("odd:name.py", 1), ("odd:name.py", 2)]
        assert [(match.path, match.line_number) for match in result.matches] == expected
        assert search_code(view, "--hidden", backend=backend).matches[0].line == "--hidden"
        assert search_code(view, ".*[literal]", backend=backend).matches[0].line == ".*[literal]"
        unicode = search_code(view, "café", backend=backend)
        assert [(match.line_number, match.line) for match in unicode.matches] == [(5, "café")]
        assert search_code(view, "no match", backend=backend).matches == []


@pytest.mark.parametrize("backend", ["python", "ripgrep"])
def test_search_path_bounds_utf8_skips_and_source_immutability(tmp_path, backend):
    if backend == "ripgrep" and shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed in validation PATH")
    source = populate(tmp_path / "source", {"a.py": "needle\n", "b.py": "needle\n", "bad.py": b"needle\xff", "large.py": "needle" * 100,
        ".env": "needle", "nested/secrets.prod": "needle", "cert/private.KEY": "needle", "cert/server.PEM": "needle", ".git/config": "needle",
        "__pycache__/cache.py": "needle", "journals/source.py": "needle", ".env.example": "needle"})
    external = populate(tmp_path / "outside", {"external.py": "needle"})
    external_before = snapshot(external)
    (source / "link.py").symlink_to(external / "external.py")
    (source / "linked").symlink_to(external, target_is_directory=True)
    if hasattr(os, "mkfifo"):
        os.mkfifo(source / "pipe")
    with unchanged(source) as view:
        result = search_code(view, "needle", backend=backend, max_file_size=32)
        assert result.backend == backend
        assert [match.path for match in result.matches] == [".env.example", "a.py", "b.py", "journals/source.py"]
        assert result.files_considered == 4 and result.files_skipped == 2
        assert "invalid_utf8" in result.warnings and "file_too_large" in result.warnings
        assert "external.py" not in result.model_dump_json()
        capped = search_code(view, "needle", backend=backend, max_matches=2, max_file_size=32)
        assert len(capped.matches) == 2 and capped.truncated and "match_limit" in capped.warnings
    assert snapshot(external) == external_before


@pytest.mark.parametrize("backend", ["python", "ripgrep"])
def test_long_lines_are_emitted_with_safe_utf8_byte_bound(tmp_path, backend):
    if backend == "ripgrep" and shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed in validation PATH")
    source = populate(tmp_path / "source", {"a.py": "α" * 1000 + "needle\n"})
    with unchanged(source) as view:
        result = search_code(view, "needle", backend=backend, max_line_bytes=7)
        match = result.matches[0]
        assert match.line == "α" * 3 and match.line_truncated
        assert len(match.line.encode("utf-8")) <= 7 and match.line_number == 1


def test_ripgrep_and_python_ignore_universes_are_explicit(tmp_path):
    if shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed in validation PATH")
    source = populate(tmp_path / "source", {".gitignore": "ignored.py\n", "ignored.py": "needle", "visible.py": "needle"})
    (source / ".git").mkdir()
    with unchanged(source) as view:
        native = search_code(view, "needle", backend="ripgrep")
        fallback = search_code(view, "needle", backend="python")
        assert native.backend == "ripgrep" and fallback.backend == "python"
        assert [match.path for match in native.matches] == ["visible.py"]
        assert [match.path for match in fallback.matches] == ["ignored.py", "visible.py"]
        assert "native_ripgrep_ignores" in native.warnings
        assert "explicit_policy_ignores_only" in fallback.warnings
        assert native.files_considered == 3  # Known prefilter, not claimed rg-open count.


def test_missing_ripgrep_falls_back_explicitly(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle"})
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with unchanged(source) as view:
        result = search_code(view, "needle")
        assert result.backend == "python" and result.fallback_reason == "ripgrep_unavailable"
        assert result.matches[0].path == "a.py"


@pytest.mark.parametrize("kind", ["ignore_link", "special_ignore", "metadata_file", "metadata_exclude", "truncated_inventory"])
def test_unsafe_native_ignore_lookup_never_launches_rg(tmp_path, monkeypatch, kind):
    source = populate(tmp_path / "source", {"a.py": "needle", "z.py": "needle"})
    outside = tmp_path / "outside-ignore"
    outside.write_text("a.py\n")
    if kind == "ignore_link":
        (source / ".gitignore").symlink_to(outside)
    elif kind == "special_ignore":
        if not hasattr(os, "mkfifo"):
            pytest.skip("OS has no named-pipe fixture support")
        os.mkfifo(source / ".ignore")
    elif kind == "metadata_file":
        (source / ".git").write_text("gitdir: ../outside")
    elif kind == "metadata_exclude":
        populate(source, {".git/info/exclude": "a.py\n"})
    monkeypatch.setattr(module.shutil, "which", lambda name: "/controlled/rg")
    with unchanged(source) as view:
        with monkeypatch.context() as guard:
            guard.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("unsafe lookup must not launch"))
            result = search_code(view, "needle", max_entries=1 if kind == "truncated_inventory" else 20)
        assert result.backend == "python" and result.fallback_reason == "native_ignore_safety_requires_python"
    assert outside.read_text() == "a.py\n"


def fake_rg(monkeypatch, script):
    """Controlled test process, never repository-controlled executable code."""
    real_popen = module.subprocess.Popen
    calls, children = [], []
    monkeypatch.setattr(module.shutil, "which", lambda name: "/controlled/rg")

    def launch(args, **kwargs):
        calls.append((args, kwargs))
        process = real_popen([sys.executable, "-c", script], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    return calls, children


def event(path="a.py", line="needle\n", number=1):
    return {"type": "match", "data": {"path": {"text": path}, "lines": {"text": line}, "line_number": number}}


def test_streamed_json_literal_argv_and_global_cap_termination(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "--hidden\n"})
    payload = (json.dumps(event(line="--hidden\n")) + "\n").encode()
    script = f"import sys,time\nsys.stdout.buffer.write({payload[:5]!r});sys.stdout.flush();time.sleep(.02)\nsys.stdout.buffer.write({payload[5:]!r});sys.stdout.flush();time.sleep(10)\n"
    calls, children = fake_rg(monkeypatch, script)
    with unchanged(source) as view:
        result = search_code(view, "--hidden", max_matches=1, timeout=1)
        assert result.backend == "ripgrep" and result.truncated and len(result.matches) == 1
    args, kwargs = calls[0]
    assert args[-3:] == ["--", "--hidden", "."]
    assert "--json" in args and "--fixed-strings" in args and "--no-follow" in args
    assert kwargs["shell"] is False and kwargs["cwd"] == source.resolve()
    assert "--iglob" in args and "!**/.env" in args and "!**/*.pem" in args
    assert children[0].poll() is not None


def test_timeout_kills_and_reaps_process_without_mutation(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle"})
    _, children = fake_rg(monkeypatch, "import time\ntime.sleep(10)\n")
    with unchanged(source) as view:
        with pytest.raises(RepositorySearchError, match="timeout"):
            search_code(view, "needle", timeout=0.15)
    assert children[0].poll() is not None


def test_launch_failure_falls_back_but_runtime_error_is_not_hidden(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle"})
    with unchanged(source) as view:
        with monkeypatch.context() as failure:
            failure.setattr(module.shutil, "which", lambda name: "/controlled/rg")
            def fail(*a, **kw):
                raise OSError("launch failed")
            failure.setattr(module.subprocess, "Popen", fail)
            result = search_code(view, "needle")
        assert result.backend == "python" and result.fallback_reason == "ripgrep_launch_failed"
        with monkeypatch.context() as failure:
            fake_rg(failure, "import sys\nsys.stderr.write('x'*500000);sys.stderr.flush();sys.exit(2)\n")
            with pytest.raises(RepositorySearchError, match="backend_error") as error:
                search_code(view, "needle", timeout=2)
            assert len(str(error.value)) < 256


@pytest.mark.parametrize("script,code", [("import sys\nsys.stdout.write('bad json\\n')", "backend_output"), ("import sys\nsys.stdout.write('x'*20000);sys.stdout.flush()", "backend_output_limit")])
def test_malformed_or_oversized_stream_is_bounded_tool_failure(tmp_path, monkeypatch, script, code):
    source = populate(tmp_path / "source", {"a.py": "needle"})
    _, children = fake_rg(monkeypatch, script)
    with unchanged(source) as view:
        with pytest.raises(RepositorySearchError, match=code):
            search_code(view, "needle", max_file_size=16)
    assert children[0].poll() is not None


def test_encoded_invalid_utf8_and_sensitive_output_cannot_be_canonical(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle", ".env": "private fixture"})
    invalid = event()
    invalid["data"]["lines"] = {"bytes": base64.b64encode(b"needle\xff").decode()}
    payload = json.dumps(invalid) + "\n" + json.dumps(event(path=".env", line="private fixture")) + "\n"
    fake_rg(monkeypatch, f"import sys\nsys.stdout.write({payload!r})")
    with unchanged(source) as view:
        result = search_code(view, "needle")
        assert result.matches == []
        assert "invalid_utf8_output" in result.warnings and "unsafe_backend_path" in result.warnings
        assert "private fixture" not in result.model_dump_json()


def test_normal_no_match_exit_one_is_not_failure(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "other"})
    fake_rg(monkeypatch, "import sys\nsys.exit(1)")
    with unchanged(source) as view:
        result = search_code(view, "needle")
        assert result.backend == "ripgrep" and result.matches == [] and not result.truncated


def test_eof_json_match_at_cap_is_controlled_truncation(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle"})
    payload = json.dumps(event())  # Deliberately no final newline.
    _, children = fake_rg(monkeypatch, f"import sys\nsys.stdout.write({payload!r})")
    with unchanged(source) as view:
        result = search_code(view, "needle", max_matches=1)
        assert len(result.matches) == 1 and result.truncated
        assert "match_limit" in result.warnings
    assert children[0].poll() is not None


def test_started_backend_io_error_is_not_hidden_by_fallback(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle"})
    _, children = fake_rg(monkeypatch, "import sys,time\nsys.stdout.write('event');sys.stdout.flush();time.sleep(10)")
    original_read = module.os.read
    def fail_stream(descriptor, size):
        if children and descriptor == children[0].stdout.fileno():
            raise OSError("controlled stream failure after launch")
        return original_read(descriptor, size)
    monkeypatch.setattr(module.os, "read", fail_stream)
    with unchanged(source) as view:
        with pytest.raises(RepositorySearchError, match="backend_io"):
            search_code(view, "needle", timeout=1)
    assert children[0].poll() is not None


def test_python_bounded_inventory_and_unreadable_file_warnings(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "needle", "b.py": "needle"})
    original = Path.open
    with unchanged(source) as view:
        with monkeypatch.context() as failure:
            def fail(path, *a, **kw):
                if path.name == "b.py":
                    raise PermissionError("controlled unreadable file")
                return original(path, *a, **kw)
            failure.setattr(Path, "open", fail)
            result = search_code(view, "needle", backend="python")
        assert result.files_skipped == 1 and "unreadable" in result.warnings
        bounded = search_code(view, "needle", backend="python", max_entries=1)
        assert bounded.truncated and [match.path for match in bounded.matches] == ["a.py"]


@pytest.mark.parametrize("kwargs", [{"max_matches": 0}, {"max_file_size": False}, {"timeout": float("nan")}, {"timeout": 0}, {"case_sensitive": 1}, {"backend": "unknown"}])
def test_invalid_search_options_raise_typed_error(tmp_path, kwargs):
    with pytest.raises(RepositoryError):
        search_code(RepositoryView(tmp_path), "needle", **kwargs)
