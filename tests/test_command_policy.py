from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestration.contracts import ValidationCommand
from services.command_policy import ValidationPolicyError, validate_command
from services.validation import run_validation
from services.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "src").mkdir()
    (source / "tests/test_example.py").write_text("def test_case(): pass\n")
    (source / "-test.py").write_text("def test_case(): pass\n")
    (source / "semi;$(text).py").write_text("def test_case(): pass\n")
    with Workspace(source) as candidate:
        yield candidate


@pytest.mark.parametrize("argv", [
    ["pytest"], ["pytest", "-q"], ["pytest", "tests"],
    ["pytest", "tests/test_example.py::test_case"],
    ["python", "-m", "pytest", "-q", "tests/test_example.py"],
    ["pytest", "--quiet", "-x", "--exitfirst", "--disable-warnings", "--strict-config", "--strict-markers", "--collect-only"],
    *[["pytest", f"--tb={value}"] for value in ("auto", "long", "short", "line", "native", "no")],
    ["pytest", "--maxfail=1"], ["pytest", "--maxfail=100"],
    ["pytest", "tests/test_example.py::TestClass::test_case[value;with space]"],
    ["pytest", "semi;$(text).py"],
    ["ruff", "check", "src"], ["ruff", "check", "--no-cache", "--quiet", "src"],
    ["mypy", "src"], ["mypy", "--no-incremental", "--show-error-codes", "--strict", "--pretty", "--no-pretty", "src"],
])
def test_complete_supported_grammar(workspace, argv):
    result = validate_command(workspace, ValidationCommand(argv=argv))
    assert result.root == workspace.workspace_root
    assert result.family in ("pytest", "ruff", "mypy")
    if result.targets:
        assert result.logical_argv[-len(result.targets)-1] == "--"
        assert result.logical_argv[-len(result.targets):] == result.targets
    if result.family == "pytest":
        assert result.logical_argv[:3] == ("python", "-m", "pytest")


@pytest.mark.parametrize("argv", [
    ["sh", "-c", "echo bad"], ["echo", "hello"], ["/usr/bin/pytest"], ["./pytest"],
    ["python", "-c", "pass"], ["python", "script.py"], ["python", "-m", "pip"], ["python", "-m", "unittest"],
    ["ruff", "format", "src"], ["ruff", "check"], ["mypy"],
    *[["pytest", flag] for flag in ("-p", "-c", "-o", "--override-ini", "--rootdir", "--basetemp", "--pyargs", "--import-mode", "-k", "-m", "-qq", "--tb", "--tb=bad", "--maxfail", "--maxfail=0", "--maxfail=101", "--maxfail=-1", "--maxfail=01")],
    ["pytest", "--rootdir=tests"], ["pytest", "--maxfail", "tests"],
    ["ruff", "check", "--config=pyproject.toml", "src"], ["ruff", "check", "--fix", "src"],
    ["mypy", "--config-file=setup.cfg", "src"], ["mypy", "--cache-dir=outside", "src"],
    ["mypy", "--python-executable=python", "src"],
    *[["pytest", path] for path in ("../outside", "/etc/passwd", "C:/tests", "tests//test_example.py", "./tests", "tests/../src", "tests\\test_example.py", "missing.py", "tests/test_example.py::", "tests::test_case", "tests/test_example.py::::test_case", "tests/test_example.py::test_case[]", "tests/test_example.py::test_case[unclosed")],
    ["mypy", "src::test_case"], ["pytest", "-test.py"], ["pytest", "--", "-test.py"], ["pytest", "\t"],
    ["pytest", "x" * 4097], ["pytest", *["-q"] * 64],
])
def test_rejected_requests_never_launch(workspace, monkeypatch, argv):
    monkeypatch.setattr("services.validation.subprocess.Popen", lambda *a, **kw: pytest.fail("rejected request launched"))
    with pytest.raises(ValidationPolicyError):
        run_validation(workspace, "patch-1", ValidationCommand(argv=argv))


@pytest.mark.parametrize("parent", [False, True])
def test_symlink_targets_are_rejected_before_launch(workspace, tmp_path, monkeypatch, parent):
    external = tmp_path / "outside"
    external.mkdir()
    (external / "test_out.py").write_text("raise AssertionError\n")
    path = workspace.workspace_root / ("linked" if parent else "linked.py")
    path.symlink_to(external if parent else external / "test_out.py", target_is_directory=parent)
    target = "linked/test_out.py" if parent else "linked.py"
    monkeypatch.setattr("services.validation.subprocess.Popen", lambda *a, **kw: pytest.fail("symlink request launched"))
    with pytest.raises(ValidationPolicyError, match="symlink_target"):
        run_validation(workspace, "patch-1", ValidationCommand(argv=["pytest", target]))


@pytest.mark.parametrize("kind", ["forged", "forged_owner", "disposed", "redirected", "missing", "subclass"])
def test_only_live_concrete_owned_workspaces_are_accepted(workspace, monkeypatch, kind):
    target = workspace
    if kind == "forged":
        target = object.__new__(Workspace)
    elif kind == "forged_owner":
        target = object.__new__(Workspace)
        target._Workspace__owner = SimpleNamespace(name=str(workspace.workspace_root.parent))
        target._Workspace__disposed = False
        target._Workspace__workspace_root = workspace.workspace_root
        target._Workspace__source_root = workspace.source_root
    elif kind == "disposed":
        workspace.dispose()
    elif kind == "redirected":
        monkeypatch.setattr(workspace, "_Workspace__workspace_root", workspace.source_root)
    elif kind == "missing":
        import shutil
        shutil.rmtree(workspace.workspace_root)
    else:
        target = SimpleNamespace(_require_live=lambda: workspace.workspace_root)
    monkeypatch.setattr("services.validation.subprocess.Popen", lambda *a, **kw: pytest.fail("invalid workspace launched"))
    with pytest.raises(ValidationPolicyError, match="invalid_workspace"):
        run_validation(target, "patch-1", ValidationCommand(argv=["pytest"]))


@pytest.mark.parametrize("value", [[], [123], ["pytest", "bad\x00target"], ["pytest", "\ud800"]])
def test_constructed_command_bypasses_are_revalidated(workspace, monkeypatch, value):
    command = ValidationCommand.model_construct(argv=value)
    monkeypatch.setattr("services.validation.subprocess.Popen", lambda *a, **kw: pytest.fail("invalid schema launched"))
    with pytest.raises(ValidationPolicyError, match="invalid_command"):
        run_validation(workspace, "patch-1", command)


def test_mutated_collection_is_revalidated(workspace, monkeypatch):
    command = ValidationCommand(argv=["pytest"])
    command.argv.append(123)
    monkeypatch.setattr("services.validation.subprocess.Popen", lambda *a, **kw: pytest.fail("mutated schema launched"))
    with pytest.raises(ValidationPolicyError, match="invalid_command"):
        run_validation(workspace, "patch-1", command)
