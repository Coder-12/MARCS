"""Bounded literal search, not query generation, ranking, or retrieved context.

Case-insensitive v1 means ASCII folding; other Unicode characters remain literal
in both backends. Ripgrep honors native local ignores; Python uses explicit path
policy only. Unsafe/incomplete native-ignore preflight falls back explicitly.
Only the fixed rg invocation is executed; repository code never runs.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import time

from orchestration.contracts import CodeSearchMatch, CodeSearchResult, RepositoryEntryKind, SearchBackend
from services.repository import CACHE_DIRS, DEFAULT_MAX_ENTRIES, RepositoryError, RepositoryView, _positive

DEFAULT_MAX_MATCHES = 100
DEFAULT_MAX_FILE_SIZE = 262144
DEFAULT_MAX_LINE_BYTES = 2048
DEFAULT_TIMEOUT = 5.0
DEFAULT_MAX_QUERY_BYTES = 4096
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class RepositorySearchError(RepositoryError):
    pass


class _RipgrepLaunchError(RepositorySearchError):
    pass


def _deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise RepositorySearchError("timeout", "bounded search timed out")


def _text_file(view: RepositoryView, path: str, max_file_size: int) -> str:
    target = view._file(path)
    try:
        with target.open("rb") as stream:
            raw = stream.read(max_file_size + 1)
        if len(raw) > max_file_size:
            raise RepositoryError("file_too_large", "file exceeds search byte bound")
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RepositoryError("invalid_utf8", "search candidate is not UTF-8") from exc
    except OSError as exc:
        raise RepositoryError("unreadable", "cannot read search candidate") from exc


def _match(path: str, number: int, line: str, max_line_bytes: int) -> CodeSearchMatch:
    raw = line.encode("utf-8")
    limited = codecs.getincrementaldecoder("utf-8")("strict").decode(raw[:max_line_bytes], final=False)
    return CodeSearchMatch(path=path, line_number=number, line=limited,
                           line_truncated=len(raw) > max_line_bytes)


def _native_safe(view: RepositoryView, inventory) -> bool:
    """Native ignore lookup must not follow links or read blocked Git metadata.

    A bounded/incomplete tree cannot establish this, so use the explicit-policy
    backend instead. No sensitive metadata contents are inspected here.
    """
    if inventory.truncated or inventory.unreadable_entry_count:
        return False
    for entry in inventory.entries:
        name = entry.path.rsplit("/", 1)[-1].casefold()
        if entry.kind == RepositoryEntryKind.SYMLINK and name in (".ignore", ".rgignore", ".gitignore"):
            return False
        if entry.kind == RepositoryEntryKind.FILE and name in CACHE_DIRS:
            return False
    directories = [view.root] + [view.root / entry.path for entry in inventory.entries
                                  if entry.kind == RepositoryEntryKind.DIRECTORY]
    for directory in directories:
        for name in (".gitignore", ".ignore", ".rgignore"):
            try:
                ignore = (directory / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return False
            if not stat.S_ISREG(ignore.st_mode):
                return False
        metadata = directory / ".git"
        try:
            info = metadata.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode):
            return False
        # rg can read info/exclude as ignore rules; that is blocked repository data.
        info_dir = metadata / "info"
        if info_dir.is_symlink() or os.path.lexists(info_dir / "exclude"):
            return False
    return True


def _glob_literal(path: str) -> str:
    return "".join("\\" + char if char in "[]{}*?" else char for char in path)


def _argv(binary: str, query: str, case_sensitive: bool, max_file_size: int, excluded: list[str]) -> list[str]:
    args = [binary, "--no-config", "--json", "--hidden", "--fixed-strings", "--no-follow", "--text",
            "--no-ignore-parent", "--no-ignore-global", "--no-require-git", "--encoding", "none",
            "--no-unicode", "--sort", "path", "--max-filesize", str(max_file_size),
            "--case-sensitive" if case_sensitive else "--ignore-case"]
    for pattern in (".git", ".env", "secrets.*", "*.key", "*.pem", *sorted(CACHE_DIRS)):
        args.extend(["--iglob", f"!**/{pattern}", "--iglob", f"!**/{pattern}/**"])
    for path in excluded:
        args.extend(["--glob", "!/" + _glob_literal(path)])
    return args + ["--", query, "."]


def _stop(process) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.25)


def _json_text(value: object) -> str:
    if not isinstance(value, dict):
        raise ValueError("missing structured text")
    if isinstance(value.get("text"), str):
        value["text"].encode("utf-8", errors="strict")
        return value["text"]
    if isinstance(value.get("bytes"), str):
        return base64.b64decode(value["bytes"], validate=True).decode("utf-8", errors="strict")
    raise ValueError("missing structured text")


def _ripgrep(view: RepositoryView, args: list[str], paths: set[str], deadline: float,
             max_matches: int, max_file_size: int, max_line_bytes: int):
    try:
        process = subprocess.Popen(args, cwd=view.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False, env={"PATH": os.defpath, "LANG": "C"})
    except OSError as exc:
        raise _RipgrepLaunchError("backend_launch", "cannot launch ripgrep") from exc
    matches, warnings = [], set()
    buffer = bytearray()
    event_limit = max_file_size * 6 + 16384
    capped = False

    def event(raw: bytes):
        nonlocal capped
        try:
            item = json.loads(raw.decode("utf-8", errors="strict"))
            if item.get("type") != "match":
                return
            data = item["data"]
            path = _json_text(data["path"])
            if path.startswith("./"):
                path = path[2:]
            try:
                view._file(path)
            except RepositoryError:
                warnings.add("unsafe_backend_path")
                return
            if path not in paths:
                warnings.add("unprefiltered_backend_path")
                return
            line = _json_text(data["lines"]).removesuffix("\n").removesuffix("\r")
            number = data["line_number"]
            if type(number) is not int or number < 1:
                raise ValueError("invalid source line")
            matches.append(_match(path, number, line, max_line_bytes))
            if len(matches) >= max_matches:
                capped = True
        except UnicodeError:
            warnings.add("invalid_utf8_output")
        except (ValueError, KeyError, TypeError, AttributeError, binascii.Error) as exc:
            raise RepositorySearchError("backend_output", "invalid ripgrep JSON event") from exc

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() and not capped:
                _deadline(deadline)
                for key, _ in selector.select(timeout=min(0.05, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stderr":
                        # Drain without retaining source/error payloads or risking pipe deadlock.
                        continue
                    buffer.extend(chunk)
                    while b"\n" in buffer and not capped:
                        raw, _, remainder = buffer.partition(b"\n")
                        if len(raw) > event_limit:
                            raise RepositorySearchError("backend_output_limit", "ripgrep event exceeds output bound")
                        buffer[:] = remainder
                        event(raw)
                    if len(buffer) > event_limit:
                        raise RepositorySearchError("backend_output_limit", "ripgrep event exceeds output bound")
            if capped:
                warnings.add("match_limit")
                _stop(process)
            else:
                if buffer:
                    event(bytes(buffer))
                if capped:
                    warnings.add("match_limit")
                    _stop(process)
                    return matches, capped, warnings
                _deadline(deadline)
                try:
                    code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise RepositorySearchError("timeout", "bounded ripgrep wait timed out") from exc
                if code not in (0, 1):
                    raise RepositorySearchError("backend_error", f"ripgrep exited with status {code}")
        return matches, capped, warnings
    except OSError as exc:
        raise RepositorySearchError("backend_io", "ripgrep stream handling failed") from exc
    finally:
        _stop(process)
        process.stdout.close()
        process.stderr.close()


def search_code(view: RepositoryView, query: str, *, case_sensitive: bool = False,
                backend: SearchBackend | str | None = None, max_matches: int = DEFAULT_MAX_MATCHES,
                max_file_size: int = DEFAULT_MAX_FILE_SIZE, timeout: float = DEFAULT_TIMEOUT,
                max_line_bytes: int = DEFAULT_MAX_LINE_BYTES, max_query_bytes: int = DEFAULT_MAX_QUERY_BYTES,
                max_entries: int = DEFAULT_MAX_ENTRIES) -> CodeSearchResult:
    """One literal LF-oriented query; no smart-case or task-conditioned policy.

    Unavailable/unsafe prelaunch rg falls back with a reason. Once rg starts,
    material errors raise RepositorySearchError; they are never silently hidden.
    """
    if type(view) is not RepositoryView:
        raise RepositorySearchError("invalid_root", "search requires a RepositoryView")
    for value, name in ((max_matches, "max_matches"), (max_file_size, "max_file_size"),
                        (max_line_bytes, "max_line_bytes"), (max_query_bytes, "max_query_bytes")):
        _positive(value, name)
    if not isinstance(query, str) or not query.strip() or any(char in query for char in ("\x00", "\r", "\n")):
        raise RepositorySearchError("invalid_query", "query must be one nonblank literal line")
    try:
        query_bytes = query.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RepositorySearchError("invalid_query", "query must be UTF-8") from exc
    if len(query_bytes) > max_query_bytes:
        raise RepositorySearchError("invalid_query", "query exceeds byte bound")
    if type(case_sensitive) is not bool or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise RepositorySearchError("invalid_limit", "case mode must be bool and timeout finite/positive")
    if backend not in (None, SearchBackend.PYTHON, SearchBackend.RIPGREP):
        raise RepositorySearchError("invalid_backend", "unknown search backend")
    deadline = time.monotonic() + timeout
    inventory = view.inventory(max_entries=max_entries)
    warnings = set(inventory.warnings)
    paths, excluded = [], []
    for entry in sorted(inventory.entries, key=lambda entry: entry.path):
        if entry.kind != RepositoryEntryKind.FILE:
            continue
        _deadline(deadline)
        try:
            if entry.size_bytes > max_file_size:
                raise RepositoryError("file_too_large", "file exceeds search byte bound")
            _text_file(view, entry.path, max_file_size)
            paths.append(entry.path)
        except RepositoryError as exc:
            excluded.append(entry.path)
            warnings.add(exc.code)
    fallback = None
    binary = shutil.which("rg") if backend != SearchBackend.PYTHON else None
    selected = SearchBackend.PYTHON if binary is None else SearchBackend.RIPGREP
    if backend != SearchBackend.PYTHON and binary is None:
        fallback = "ripgrep_unavailable"
    if selected == SearchBackend.RIPGREP and not _native_safe(view, inventory):
        selected, fallback = SearchBackend.PYTHON, "native_ignore_safety_requires_python"
    args = _argv(binary, query, case_sensitive, max_file_size, excluded) if selected == SearchBackend.RIPGREP else []
    if args and sum(len(arg.encode("utf-8")) + 1 for arg in args) > 65536:
        selected, fallback = SearchBackend.PYTHON, "argv_limit_requires_python"
    matches, capped = [], False
    if selected == SearchBackend.RIPGREP and paths:
        try:
            matches, capped, rg_warnings = _ripgrep(view, args, set(paths), deadline,
                                                   max_matches, max_file_size, max_line_bytes)
            warnings.update(rg_warnings)
        except _RipgrepLaunchError:
            selected, fallback = SearchBackend.PYTHON, "ripgrep_launch_failed"
    if selected == SearchBackend.PYTHON:
        needle = query if case_sensitive else query.translate(_ASCII_FOLD)
        for path in paths:
            _deadline(deadline)
            try:
                text = _text_file(view, path, max_file_size)
            except RepositoryError as exc:
                excluded.append(path)
                warnings.add(exc.code)
                continue
            for number, line in enumerate(text.split("\n"), 1):
                line = line.removesuffix("\r")
                comparable = line if case_sensitive else line.translate(_ASCII_FOLD)
                if needle in comparable:
                    matches.append(_match(path, number, line, max_line_bytes))
                    if len(matches) >= max_matches:
                        capped = True
                        warnings.add("match_limit")
                        break
            if capped:
                break
    if fallback:
        warnings.add("backend_fallback")
    warnings.add("native_ripgrep_ignores" if selected == SearchBackend.RIPGREP else "explicit_policy_ignores_only")
    return CodeSearchResult(query=query, case_sensitive=case_sensitive, backend=selected,
        matches=sorted(matches, key=lambda match: (match.path, match.line_number)),
        truncated=inventory.truncated or capped, files_considered=len(paths), files_skipped=len(set(excluded)),
        warnings=sorted(warnings), fallback_reason=fallback)
