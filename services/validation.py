"""Controlled validation of trusted local repositories, not a security sandbox.

Only fixed isolated host modules execute. Plugins/config/tests still run with
host-user permissions and can access files/network, print secrets, or detach
children. Output is bounded, not sanitized. After timeout/infrastructure failure,
treat the candidate as potentially tainted and normally discard it.
"""
from __future__ import annotations

import codecs
import importlib.machinery
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time

from orchestration.contracts import ValidationCommand, ValidationResult
from services.command_policy import ValidationPolicyError, _PermittedCommand, validate_command
from services.workspace import Workspace, WorkspaceError

DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 300.0
DEFAULT_OUTPUT_BYTES = 65536
MAX_OUTPUT_BYTES = 1048576
TERMINATION_GRACE_S = 0.2
REAP_TIMEOUT_S = 0.5
POST_EXIT_DRAIN_S = 0.25
_PYTHON = Path(sys.executable).absolute()  # Preserve the venv spelling, not its resolved base binary.


class ValidationExecutionError(Exception):
    """Infrastructure failure; never a test failure or synthetic exit status."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _execution_argv(command: _PermittedCommand) -> tuple[str, ...]:
    """Resolve installed top-level modules without cwd/sys.path module lookup.

    Standard host site directories are trusted; arbitrary inherited PYTHONPATH is
    not. This deliberately does not provision environments or support arbitrary
    module names. Host site/.pth/plugins remain part of the trust boundary.
    """
    try:
        resolved = _PYTHON.resolve(strict=True)
        if not _PYTHON.is_absolute() or resolved.is_relative_to(command.root) or _PYTHON.is_relative_to(command.root):
            raise ValidationExecutionError("tool_unavailable", "trusted Python is not outside the candidate")
        if not resolved.is_file() or not os.access(_PYTHON, os.X_OK):
            raise ValidationExecutionError("tool_unavailable", "trusted interpreter is not executable")
        sites = sorted({sysconfig.get_path(name) for name in ("purelib", "platlib")})
        spec = importlib.machinery.PathFinder.find_spec(command.family, sites)
        locations = list(spec.submodule_search_locations or []) if spec else []
        if not locations or not any((Path(path) / "__main__.py").is_file() for path in locations):
            raise ValidationExecutionError("tool_unavailable", "validator module is not installed in trusted host sites")
        if any(Path(path).resolve().is_relative_to(command.root) for path in locations):
            raise ValidationExecutionError("tool_unavailable", "candidate-local validator modules are forbidden")
    except (OSError, RuntimeError, ImportError) as exc:
        raise ValidationExecutionError("tool_unavailable", "cannot establish trusted validator availability") from exc
    prefix = ("check",) if command.family == "ruff" else ()
    return (str(_PYTHON), "-I", "-B", "-X", "utf8", "-m", command.family,
            *prefix, *command.flags, *(("--", *command.targets) if command.targets else ()))


def _environment(storage: Path, command: _PermittedCommand) -> dict[str, str]:
    home, temporary, cache = (storage / name for name in ("home", "tmp", "cache"))
    for path in (home, temporary, cache):
        path.mkdir()
    # Fixed absolute host directories; never inherit PATH or include cwd/empty entries.
    paths = [_PYTHON.parent, *(Path(path) for path in os.defpath.split(os.pathsep) if path)]
    if any(not path.is_absolute() or path.resolve().is_relative_to(command.root) for path in paths):
        raise ValidationExecutionError("environment_error", "host PATH must exclude candidate and relative directories")
    env = {"PATH": os.pathsep.join(dict.fromkeys(map(str, paths))), "HOME": str(home),
           "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary),
           "XDG_CACHE_HOME": str(cache), "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name == "nt":
        # Required Windows host values only; still no credentials or Python/plugin overrides.
        system = os.environ.get("SystemRoot")
        if not system or not Path(system).is_absolute():
            raise ValidationExecutionError("environment_error", "Windows requires an absolute host SystemRoot")
        env.update(SystemRoot=system, WINDIR=system, USERPROFILE=str(home), LOCALAPPDATA=str(cache))
    return env


def _terminate(process: subprocess.Popen) -> None:
    """Bounded ordinary process-group cleanup; detached/adversarial children excluded."""
    try:
        if os.name == "posix":
            sent = False
            try:
                os.killpg(process.pid, signal.SIGTERM)
                sent = True
            except ProcessLookupError:
                pass
            if sent:
                grace_deadline = time.monotonic() + TERMINATION_GRACE_S
                try:
                    # Reap a cooperative direct child before signalling the remaining
                    # group: Darwin returns EPERM for an unreaped zombie-only group.
                    process.wait(timeout=TERMINATION_GRACE_S)
                except subprocess.TimeoutExpired:
                    pass
                time.sleep(max(0, grace_deadline - time.monotonic()))
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        elif process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=TERMINATION_GRACE_S)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait(timeout=REAP_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationExecutionError("process_cleanup", "essential process cleanup failed") from exc


def _capture(process: subprocess.Popen, timeout_s: float, limits: tuple[int, int]):
    """Poll both nonblocking pipes in fixed chunks; retain caps, drain excess.

    Nonblocking pipe handles require Python/platform support (Python 3.12 on
    Windows). No unbounded reader threads or blocking post-exit communicate().
    """
    streams = (process.stdout, process.stderr)
    retained = [bytearray(), bytearray()]
    truncated = [False, False]
    open_streams = {0, 1}
    deadline = time.monotonic() + timeout_s
    post_exit_deadline = None
    timed_out = False
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
    while open_streams or process.poll() is None:
        now = time.monotonic()
        code = process.poll()
        if code is not None and post_exit_deadline is None:
            post_exit_deadline = now + POST_EXIT_DRAIN_S
        if code is None and now >= deadline and not timed_out:
            timed_out = True
            _terminate(process)
            post_exit_deadline = time.monotonic() + POST_EXIT_DRAIN_S
        if open_streams and post_exit_deadline is not None and time.monotonic() >= post_exit_deadline:
            raise ValidationExecutionError("pipe_cleanup", "output pipes remained open beyond post-exit drain budget")
        progress = False
        for index in sorted(open_streams):
            try:
                chunk = os.read(streams[index].fileno(), 4096)
            except BlockingIOError:
                continue
            if not chunk:
                open_streams.remove(index)
                continue
            progress = True
            remaining = limits[index] - len(retained[index])
            retained[index].extend(chunk[:remaining])
            truncated[index] |= len(chunk) > remaining
        if not progress:
            time.sleep(0.005)
    return retained, truncated, timed_out


def _decode(raw: bytearray, truncated: bool) -> str:
    try:
        # A cap may split only the final codepoint. Invalid retained bytes still fail.
        return codecs.getincrementaldecoder("utf-8")("strict").decode(bytes(raw), final=not truncated)
    except UnicodeError as exc:
        raise ValidationExecutionError("output_encoding", "retained validation output is not UTF-8") from exc


def run_validation(workspace: Workspace, patch_id: str, command: ValidationCommand, *,
                   timeout_s: float = DEFAULT_TIMEOUT_S,
                   stdout_limit_bytes: int = DEFAULT_OUTPUT_BYTES,
                   stderr_limit_bytes: int = DEFAULT_OUTPUT_BYTES) -> ValidationResult:
    """No retries or status transitions. Infrastructure failures raise typed errors.

    Logical pytest aliases normalize to python -m pytest; execution adds fixed
    -I -B -X utf8. Ruff/mypy also use installed isolated host modules. Pytest
    plugin autoload remains enabled for installed plugins such as pytest-asyncio.
    """
    if not isinstance(patch_id, str) or not patch_id.strip():
        raise ValidationPolicyError("invalid_patch_id", "candidate identity must be a strict nonblank string")
    try:
        patch_id.encode("utf-8")
    except UnicodeError as exc:
        raise ValidationPolicyError("invalid_patch_id", "candidate identity must be UTF-8") from exc
    if type(timeout_s) not in (float, int) or not math.isfinite(timeout_s) or not 0 < timeout_s <= MAX_TIMEOUT_S:
        raise ValidationPolicyError("invalid_limit", "timeout must be finite and within 0..300 seconds")
    for limit in (stdout_limit_bytes, stderr_limit_bytes):
        if type(limit) is not int or not 1 <= limit <= MAX_OUTPUT_BYTES:
            raise ValidationPolicyError("invalid_limit", "retained stream budgets must be 1..1048576 bytes")
    permitted = validate_command(workspace, command)
    argv = _execution_argv(permitted)
    process = None
    try:
        storage = Path(tempfile.gettempdir()).resolve(strict=True)
        if storage.is_relative_to(permitted.root) or storage.is_relative_to(workspace.source_root):
            raise ValidationExecutionError("temporary_storage", "execution storage must be outside source and candidate")
        with tempfile.TemporaryDirectory(prefix="marcs-validation-", dir=storage) as owner:
            env = _environment(Path(owner), permitted)
            # Recheck liveness just before launch; no source-repository fallback.
            try:
                if workspace._require_live() != permitted.root:
                    raise WorkspaceError("candidate ownership changed")
            except WorkspaceError as exc:
                raise ValidationPolicyError("invalid_workspace", "candidate ownership changed before launch") from exc
            started = time.monotonic()
            try:
                process = subprocess.Popen(argv, cwd=permitted.root, env=env, shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=os.name == "posix")
            except OSError as exc:
                raise ValidationExecutionError("launch_failed", "trusted validator could not start") from exc
            try:
                raw, truncated, timed_out = _capture(process, timeout_s, (stdout_limit_bytes, stderr_limit_bytes))
            finally:
                try:
                    _terminate(process)
                finally:
                    process.stdout.close()
                    process.stderr.close()
            return ValidationResult(patch_id=patch_id, argv=list(permitted.logical_argv),
                exit_code=None if timed_out else process.returncode, timed_out=timed_out,
                stdout=_decode(raw[0], truncated[0]), stderr=_decode(raw[1], truncated[1]),
                stdout_truncated=truncated[0], stderr_truncated=truncated[1],
                duration_ms=(time.monotonic() - started) * 1000)
    except ValidationExecutionError:
        raise
    except OSError as exc:
        raise ValidationExecutionError("execution_io", "execution I/O or temporary cleanup failed") from exc
