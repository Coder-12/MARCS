"""Trusted synthetic execution fixtures; no arbitrary user repository smoke tests."""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

import pytest

from orchestration.contracts import ValidationCommand, ValidationResult
from services import validation as module
from services.command_policy import ValidationPolicyError
from services.validation import ValidationExecutionError, run_validation
from services.workspace import Workspace


def snapshot(root):
    return {path.relative_to(root).as_posix(): (stat.S_IMODE(path.stat().st_mode), path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()}


@contextmanager
def candidate(tmp_path, files):
    source = tmp_path / "source"
    source.mkdir()
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    before = snapshot(source)
    try:
        with Workspace(source) as workspace:
            yield workspace
    finally:
        assert snapshot(source) == before


def validate(workspace, *args, **limits):
    return run_validation(workspace, "patch-1", ValidationCommand(argv=["pytest", *args]), **limits)


@pytest.mark.parametrize("passing", [True, False])
def test_real_pytest_success_failure_and_portable_evidence(tmp_path, passing):
    with candidate(tmp_path, {"test_example.py": f"def test_case():\n    assert {passing}\n"}) as workspace:
        result = validate(workspace, "-q", "test_example.py", timeout_s=10)
        assert result.exit_code == (0 if passing else 1)
        assert result.passed is passing and not result.timed_out
        assert result.argv == ["python", "-m", "pytest", "-q", "--", "test_example.py"]
        assert ("1 passed" if passing else "1 failed") in result.stdout
        assert result.duration_ms >= 0
        assert ValidationResult.model_validate(json.loads(json.dumps(result.model_dump(mode="json")))) == result


def test_no_target_pytest_imports_modules_and_installed_async_plugin(tmp_path):
    files = {"tests/__init__.py": "", "app.py": "VALUE=42\n", "tests/test_app.py":
        "import app,pytest\n@pytest.mark.asyncio\nasync def test_import():\n    assert app.VALUE==42\n"}
    with candidate(tmp_path, files) as workspace:
        result = validate(workspace, "-q", timeout_s=10)
        assert result.passed and "1 passed" in result.stdout


def test_candidate_cwd_environment_owned_storage_and_source_immutability(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "DATABASE_URL", "PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        monkeypatch.setenv(key, "fake-parent-value")
    files = {"test_environment.py": '''import json,os,sys
from pathlib import Path
def test_environment():
    keys=("OPENAI_API_KEY","ANTHROPIC_API_KEY","AWS_ACCESS_KEY_ID","DATABASE_URL","PYTHONPATH","PYTHONHOME","PYTEST_ADDOPTS","PYTEST_PLUGINS")
    assert not any(key in os.environ for key in keys)
    assert all(os.environ[key]=="1" for key in ("PYTHONUTF8","PYTHONNOUSERSITE","PYTHONDONTWRITEBYTECODE"))
    assert sys.flags.isolated and sys.flags.utf8_mode and sys.dont_write_bytecode
    Path("candidate_created.txt").write_text("candidate only")
    Path("environment.json").write_text(json.dumps({key:os.environ[key] for key in ("HOME","TMPDIR","TMP","TEMP","XDG_CACHE_HOME","PATH")}))
'''}
    with candidate(tmp_path, files) as workspace:
        result = validate(workspace, "-q", timeout_s=10)
        assert result.passed, result.stdout
        assert (workspace.workspace_root / "candidate_created.txt").read_text() == "candidate only"
        assert not (workspace.source_root / "candidate_created.txt").exists()
        env = json.loads((workspace.workspace_root / "environment.json").read_text())
        for key in ("HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME"):
            path = Path(env[key])
            assert not path.exists() and not path.is_relative_to(workspace.workspace_root) and not path.is_relative_to(workspace.source_root)
        assert all(Path(path).is_absolute() and not Path(path).is_relative_to(workspace.workspace_root) for path in env["PATH"].split(os.pathsep))


@pytest.mark.parametrize("shadow", ["pytest.py", "pytest/__init__.py"])
def test_candidate_pytest_shadow_is_not_validator_module(tmp_path, shadow):
    with candidate(tmp_path, {shadow: "raise RuntimeError('CANDIDATE SHADOW EXECUTED')\n", "test_example.py": "def test_case(): pass\n"}) as workspace:
        result = validate(workspace, "-q", "test_example.py", timeout_s=10)
        assert result.passed and "1 passed" in result.stdout
        assert "CANDIDATE SHADOW EXECUTED" not in result.stdout + result.stderr


def test_candidate_executables_and_parent_path_are_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", ".")
    with candidate(tmp_path, {"pytest": "raise RuntimeError('local executable')\n", "python": "bad", "test_example.py": "def test_case(): pass\n"}) as workspace:
        result = run_validation(workspace, "patch-1", ValidationCommand(argv=["python", "-m", "pytest", "-q"]), timeout_s=10)
        assert result.passed


@pytest.mark.parametrize("target", ["semi;$(text).py", "test_example.py::test_case"])
def test_real_pytest_positional_targets_remain_data(tmp_path, target):
    path = target.partition("::")[0]
    with candidate(tmp_path, {path: "def test_case(): pass\n"}) as workspace:
        result = validate(workspace, "-q", "--", target, timeout_s=10)
        assert result.passed and "1 passed" in result.stdout
        assert result.argv[-2:] == ["--", target]


def test_dash_prefixed_target_is_rejected_before_tool_reparsing(tmp_path, monkeypatch):
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("dash target launched"))
    with candidate(tmp_path, {"-test.py": "def test_case(): pass\n"}) as workspace:
        with pytest.raises(ValidationPolicyError, match="invalid_target"):
            validate(workspace, "--", "-test.py")


def test_real_pytest_drains_heavy_both_streams_with_retention_caps(tmp_path):
    files = {"test_example.py": "def test_case(): pass\n", "conftest.py": '''import os
def pytest_sessionfinish(session, exitstatus):
    for i in range(100):
        os.write(1,b"out"*4096)
        os.write(2,b"err"*4096)
'''}
    with candidate(tmp_path, files) as workspace:
        result = validate(workspace, "-q", timeout_s=10, stdout_limit_bytes=1024, stderr_limit_bytes=1024)
        assert result.passed
        assert result.stdout_truncated and result.stderr_truncated
        assert len(result.stdout.encode("utf-8")) == len(result.stderr.encode("utf-8")) == 1024
        assert "TRUNCATED" not in result.stdout + result.stderr


def fake_process(monkeypatch, script):
    """Redirect fixed launch to a controlled test process for infrastructure branches."""
    real_popen = module.subprocess.Popen
    calls, children, storage = [], [], []
    def launch(args, **kwargs):
        calls.append((args, kwargs))
        storage.append(Path(kwargs["env"]["HOME"]).parent)
        process = real_popen([sys.executable, "-I", "-B", "-c", script], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    return calls, children, storage


def test_launch_options_and_multibyte_retention_cutoff(tmp_path, monkeypatch):
    calls, children, storage = fake_process(monkeypatch, "import os\nos.write(1,'αβγ'.encode());os.write(2,'αβγ'.encode())")
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        result = validate(workspace, "-q", stdout_limit_bytes=3, stderr_limit_bytes=3)
        assert result.stdout == result.stderr == "α"
        assert result.stdout_truncated and result.stderr_truncated and result.passed
        args, kwargs = calls[0]
        assert args[:6] == (str(module._PYTHON), "-I", "-B", "-X", "utf8", "-m")
        assert kwargs["cwd"] == workspace.workspace_root
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["shell"] is False and kwargs["close_fds"] is True
        assert kwargs["start_new_session"] == (os.name == "posix")
    assert children[0].poll() is not None and all(not path.exists() for path in storage)


@pytest.mark.parametrize("output", [b"bad\xff", b"unfinished\xc3"])
def test_invalid_retained_utf8_is_typed_infrastructure_error(tmp_path, monkeypatch, output):
    _, children, storage = fake_process(monkeypatch, f"import os\nos.write(1,{output!r})")
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationExecutionError, match="output_encoding"):
            validate(workspace)
    assert children[0].poll() is not None and all(not path.exists() for path in storage)


def test_invalid_unretained_bytes_are_not_claimed_as_validated(tmp_path, monkeypatch):
    fake_process(monkeypatch, "import os\nos.write(1,b'good'+b'\\xff'*100)")
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        result = validate(workspace, stdout_limit_bytes=4)
        assert result.stdout == "good" and result.stdout_truncated


def test_real_pytest_timeout_result_and_owned_storage_cleanup(tmp_path):
    files = {"test_slow.py": '''import os,time,json
from pathlib import Path
def test_slow():
    Path("started.json").write_text(json.dumps({"home":os.environ["HOME"],"pid":os.getpid()}))
    time.sleep(30)
'''}
    with candidate(tmp_path, files) as workspace:
        started = time.monotonic()
        result = validate(workspace, "-q", timeout_s=2)
        assert time.monotonic() - started < 5
        assert result.timed_out and result.exit_code is None and not result.passed
        assert ValidationResult.model_validate(json.loads(result.model_dump_json())) == result
        details = json.loads((workspace.workspace_root / "started.json").read_text())
        assert not Path(details["home"]).exists()
        with pytest.raises(ProcessLookupError):
            os.kill(details["pid"], 0)


def assert_stopped(pid):
    # POSIX kill(0) may observe an already-killed orphan zombie temporarily.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, text=True, timeout=1)
        if result.stdout.strip().startswith("Z"):
            return
        time.sleep(0.01)
    pytest.fail("ordinary child remained running")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group fixture")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_ordinary_descendant_cleanup_and_post_exit_pipe_budget(tmp_path, parent_exits):
    imports = '''import os,signal,subprocess,sys,time
from pathlib import Path
'''
    spawn = 'child=subprocess.Popen([sys.executable,"-c","import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"])\nPath("child.pid").write_text(str(child.pid))\n'
    # Session-finish writes already exercise real runner pipes (capture stopped).
    conftest = imports + ("def pytest_sessionfinish(session,exitstatus):\n" +
        "".join("    " + line + "\n" for line in spawn.splitlines()) + "    os._exit(0)\n" if parent_exits else spawn)
    with candidate(tmp_path, {"conftest.py": conftest, "test_slow.py": "import time\ndef test_slow(): time.sleep(30)\n" if not parent_exits else "def test_case(): pass\n"}) as workspace:
        started = time.monotonic()
        if parent_exits:
            with pytest.raises(ValidationExecutionError, match="pipe_cleanup"):
                validate(workspace, "-q", timeout_s=4)
        else:
            result = validate(workspace, "-q", timeout_s=2)
            assert result.timed_out and result.exit_code is None
        assert time.monotonic() - started < 6
        assert_stopped(int((workspace.workspace_root / "child.pid").read_text()))


@pytest.mark.parametrize("family,argv", [("ruff", ["ruff", "check", "test_example.py"]), ("mypy", ["mypy", "test_example.py"])])
def test_missing_optional_validator_never_installs_or_switches(tmp_path, monkeypatch, family, argv):
    monkeypatch.setattr(module.importlib.machinery.PathFinder, "find_spec", lambda *a: None)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("missing tool launched"))
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationExecutionError, match="tool_unavailable"):
            run_validation(workspace, "patch-1", ValidationCommand(argv=argv))


def test_launch_failure_has_typed_error_and_cleans_owned_storage(tmp_path, monkeypatch):
    paths = []
    def fail(*args, **kwargs):
        paths.append(Path(kwargs["env"]["HOME"]).parent)
        raise OSError(errno.ENOENT, "controlled failure")
    monkeypatch.setattr(module.subprocess, "Popen", fail)
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationExecutionError, match="launch_failed"):
            validate(workspace)
    assert paths and all(not path.exists() for path in paths)


def test_uncertain_process_cleanup_is_not_a_validation_result(tmp_path, monkeypatch):
    _, children, storage = fake_process(monkeypatch, "pass")
    original = module._terminate
    def fail_after_reap(process):
        original(process)
        raise ValidationExecutionError("process_cleanup", "controlled uncertainty")
    monkeypatch.setattr(module, "_terminate", fail_after_reap)
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationExecutionError, match="process_cleanup"):
            validate(workspace)
    assert children[0].poll() is not None and all(not path.exists() for path in storage)


def test_execution_storage_inside_candidate_is_rejected_before_launch(tmp_path, monkeypatch):
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(workspace.workspace_root))
        monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("unsafe storage launched"))
        with pytest.raises(ValidationExecutionError, match="temporary_storage"):
            validate(workspace)


def test_stream_io_error_cleans_process_and_storage(tmp_path, monkeypatch):
    _, children, storage = fake_process(monkeypatch, "import os,time\nos.write(1,b'out');time.sleep(30)")
    original = module.os.read
    def fail(descriptor, size):
        if children and descriptor == children[0].stdout.fileno():
            raise OSError("controlled pipe failure")
        return original(descriptor, size)
    monkeypatch.setattr(module.os, "read", fail)
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationExecutionError, match="execution_io"):
            validate(workspace)
    assert children[0].poll() is not None and all(not path.exists() for path in storage)


@pytest.mark.parametrize("patch_id", ["", " \t", 1, None, "\ud800"])
def test_invalid_identity_never_launches(tmp_path, monkeypatch, patch_id):
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("invalid identity launched"))
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationPolicyError, match="invalid_patch_id"):
            run_validation(workspace, patch_id, ValidationCommand(argv=["pytest"]))


@pytest.mark.parametrize("limits", [{"timeout_s":0},{"timeout_s":True},{"timeout_s":float("nan")},{"timeout_s":float("inf")},
    {"timeout_s":301},{"stdout_limit_bytes":0},{"stderr_limit_bytes":True},{"stdout_limit_bytes":1048577}])
def test_invalid_limits_never_launch(tmp_path, monkeypatch, limits):
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("invalid budget launched"))
    with candidate(tmp_path, {"test_example.py": "pass"}) as workspace:
        with pytest.raises(ValidationPolicyError, match="invalid_limit"):
            validate(workspace, **limits)
