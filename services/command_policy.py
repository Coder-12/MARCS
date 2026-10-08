"""Complete narrow validation grammar; declared targets, not tool read isolation.

Policy acceptance does not establish tool availability or safely contain test code.
Only trusted local repositories may be executed by the validation service.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat
import tempfile

from pydantic import ValidationError

from orchestration.contracts import ValidationCommand
from services.workspace import Workspace, WorkspaceError

MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4096
MAX_ARGV_BYTES = 16384
MAX_SELECTOR_BYTES = 1024
_PYTEST_FLAGS = frozenset(("-q", "--quiet", "-x", "--exitfirst", "--disable-warnings",
                          "--strict-config", "--strict-markers", "--collect-only"))
_RUFF_FLAGS = frozenset(("--quiet", "--no-cache"))
_MYPY_FLAGS = frozenset(("--no-incremental", "--show-error-codes", "--strict", "--pretty", "--no-pretty"))


class ValidationPolicyError(Exception):
    """Stable no-launch rejection; diagnostics never include raw task payloads."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class _PermittedCommand:
    root: Path
    family: str
    flags: tuple[str, ...]
    targets: tuple[str, ...]
    logical_argv: tuple[str, ...]


def _target(root: Path, argument: str, family: str) -> None:
    path, separator, selector = argument.partition("::")
    if (
        not path or path != path.strip() or path.startswith(("/", "-")) or "\\" in path
        or any(part in ("", ".", "..") or ":" in part or part.endswith((" ", "."))
               for part in path.split("/"))
    ):
        raise ValidationPolicyError("invalid_target", "expected a canonical repository-relative target")
    if separator:
        if family != "pytest" or len(selector.encode("utf-8")) > MAX_SELECTOR_BYTES:
            raise ValidationPolicyError("invalid_selector", "node selectors are bounded and pytest-only")
        pieces = selector.split("::")
        if len(pieces) > 8 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\[\]]+\])?", item)
                                  for item in pieces):
            raise ValidationPolicyError("invalid_selector", "unsupported pytest node selector")
    target = root
    try:
        for index, part in enumerate(path.split("/")):
            target /= part
            mode = target.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValidationPolicyError("symlink_target", "declared targets must not traverse symlinks")
            if index < len(path.split("/")) - 1 and not stat.S_ISDIR(mode):
                raise ValidationPolicyError("invalid_target", "target parent is not a directory")
        if not target.resolve(strict=True).is_relative_to(root):
            raise ValidationPolicyError("target_escape", "target is outside candidate storage")
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValidationPolicyError("invalid_target", "target must be a regular file or directory")
        if family == "pytest" and stat.S_ISREG(mode) and not path.endswith(".py"):
            raise ValidationPolicyError("invalid_target", "pytest file targets must be Python files")
        if separator and not stat.S_ISREG(mode):
            raise ValidationPolicyError("invalid_selector", "node selection requires a file target")
    except FileNotFoundError as exc:
        raise ValidationPolicyError("missing_target", "declared target does not exist") from exc
    except (OSError, RuntimeError) as exc:
        raise ValidationPolicyError("unreadable_target", "cannot establish target confinement") from exc


def validate_command(workspace: Workspace, command: ValidationCommand) -> _PermittedCommand:
    """Revalidate canonical input and the live concrete workspace before policy.

    Generated argv separates flags and targets. Top-level dash-prefixed targets
    are rejected: pytest can reparse them as options even after --. Shell
    punctuation remains ordinary filename data, not a shell command fragment.
    """
    if type(command) is not ValidationCommand:
        raise ValidationPolicyError("invalid_command", "requires a canonical ValidationCommand")
    try:
        command = ValidationCommand.model_validate(command)
    except (ValidationError, UnicodeError) as exc:
        raise ValidationPolicyError("invalid_command", "command schema revalidation failed") from exc
    if type(workspace) is not Workspace:
        raise ValidationPolicyError("invalid_workspace", "requires a concrete owned Workspace")
    # Liveness alone accepts a fabricated namespace with a name attribute. Require
    # the actual Slice-2 retained ownership handle as well, without changing Workspace.
    owner = getattr(workspace, "_Workspace__owner", None)
    if type(owner) is not tempfile.TemporaryDirectory or not getattr(getattr(owner, "_finalizer", None), "alive", False):
        raise ValidationPolicyError("invalid_workspace", "candidate has no live temporary ownership handle")
    try:
        root = workspace._require_live()
    except (WorkspaceError, AttributeError, TypeError) as exc:
        raise ValidationPolicyError("invalid_workspace", "candidate workspace is not live and owned") from exc
    argv = command.argv
    sizes = [len(argument.encode("utf-8")) for argument in argv]
    if (len(argv) > MAX_ARGUMENTS or any(size > MAX_ARGUMENT_BYTES for size in sizes)
            or sum(sizes) > MAX_ARGV_BYTES or any(not arg or any(ord(c) < 32 or ord(c) == 127 for c in arg) for arg in argv)):
        raise ValidationPolicyError("invalid_argument", "argv exceeds bounds or contains empty/control arguments")
    if argv[:3] == ["python", "-m", "pytest"]:
        family, arguments = "pytest", argv[3:]
    elif argv[0] == "pytest":
        family, arguments = "pytest", argv[1:]
    elif argv[:2] == ["ruff", "check"]:
        family, arguments = "ruff", argv[2:]
    elif argv[0] == "mypy":
        family, arguments = "mypy", argv[1:]
    else:
        raise ValidationPolicyError("command_denied", "unsupported validation command family")
    flags, targets = [], []
    positional = False
    for argument in arguments:
        if argument == "--" and not positional:
            positional = True
            continue
        if argument.startswith("-") and not positional:
            allowed = {"pytest": _PYTEST_FLAGS, "ruff": _RUFF_FLAGS, "mypy": _MYPY_FLAGS}[family]
            valid = argument in allowed
            if family == "pytest":
                valid |= bool(re.fullmatch(r"--tb=(auto|long|short|line|native|no)", argument))
                match = re.fullmatch(r"--maxfail=([1-9][0-9]{0,2})", argument)
                valid |= match is not None and int(match[1]) <= 100
            if not valid:
                raise ValidationPolicyError("flag_denied", "unsupported flag or flag value")
            flags.append(argument)
        else:
            _target(root, argument, family)
            targets.append(argument)
    if family != "pytest" and not targets:
        raise ValidationPolicyError("missing_target", "validator requires at least one declared target")
    prefix = ("python", "-m", "pytest") if family == "pytest" else (("ruff", "check") if family == "ruff" else ("mypy",))
    logical = (*prefix, *flags, *(("--", *targets) if targets else ()))
    return _PermittedCommand(root, family, tuple(flags), tuple(targets), logical)
