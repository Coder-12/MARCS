"""Read-only Python-MVP repository facts, not task-conditioned retrieval.

Internal links/special entries are never read. Counts cover observed entries;
partial inventory is explicit. Sorted bounded child selection retains bounded
memory but must enumerate directory names to establish a deterministic prefix.
No hostile concurrent filesystem-race resistance is claimed.
"""
from __future__ import annotations

import codecs
import configparser
from fnmatch import fnmatchcase
import heapq
import os
from pathlib import Path
import re
import stat
import tomllib

from orchestration.contracts import (
    RepoProfile, RepositoryAnalysisResult, RepositoryEntry, RepositoryEntryKind,
    RepositoryFactEvidence, RepositoryInventory, RepositoryText,
)

DEFAULT_MAX_ENTRIES = 2000
DEFAULT_MAX_NODES = 8000
DEFAULT_MAX_DEPTH = 16
DEFAULT_READ_BYTES = 65536
DEFAULT_SCAN_BYTES = 1048576
CACHE_DIRS = frozenset(name.casefold() for name in (
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".tox", ".nox",
    ".venv", "venv", "ENV", "node_modules", "build", "dist",
))
_SECRET_PATTERNS = ("secrets.*", "*.key", "*.pem")


class RepositoryError(Exception):
    """Stable issue code with a concise diagnostic; no raw source/error dumps."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _positive(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise RepositoryError("invalid_limit", f"{name} must be a positive integer")


def _blocked(path: str) -> bool:
    return any(part.casefold() in (".git", ".env") or any(
        fnmatchcase(part.casefold(), pattern) for pattern in _SECRET_PATTERNS
    ) for part in path.split("/"))


def _path_parts(path: str) -> list[str]:
    if (
        not isinstance(path, str) or not path or path != path.strip()
        or path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path)
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in path.split("/"))
        or (os.name == "nt" and ":" in path)
    ):
        raise RepositoryError("invalid_path", "expected a canonical repository-relative path")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RepositoryError("invalid_path", "path is not UTF-8") from exc
    return path.split("/")


def _lines(text: str) -> list[str]:
    """LF-based lines, preserving CRLF and all source characters."""
    pieces = text.split("\n")
    return [part + "\n" for part in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def _utf8_prefix(raw: bytes, limit: int) -> str:
    # Incremental decoding omits only an incomplete final codepoint, never bad bytes.
    return codecs.getincrementaldecoder("utf-8")("strict").decode(raw[:limit], final=False)


class RepositoryView:
    """Caller-owned root, canonicalized once. No mutation/cleanup/workspace API."""

    __slots__ = ("__root",)

    def __init__(self, root: str | os.PathLike[str]):
        try:
            resolved = Path(root).resolve(strict=True)
            if not resolved.is_dir():
                raise RepositoryError("invalid_root", "root must be an existing directory")
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise RepositoryError("invalid_root", "cannot establish repository root") from exc
        self.__root = resolved

    @property
    def root(self) -> Path:
        return self.__root

    def _require_root(self) -> Path:
        try:
            if self.__root.is_symlink() or not self.__root.is_dir() or self.__root.resolve(strict=True) != self.__root:
                raise RepositoryError("invalid_root", "canonical root is no longer available")
        except (OSError, RuntimeError) as exc:
            raise RepositoryError("invalid_root", "canonical root is no longer available") from exc
        return self.__root

    def _file(self, path: str) -> Path:
        parts = _path_parts(path)
        if _blocked(path) or any(part.casefold() in CACHE_DIRS for part in parts[:-1]):
            raise RepositoryError("excluded_policy", "content access excluded by path policy")
        root = self._require_root()
        target = root
        try:
            for index, part in enumerate(parts):
                target /= part
                mode = target.lstat().st_mode
                if stat.S_ISLNK(mode):
                    raise RepositoryError("symlink", "internal symlinks are structural only")
                if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                    raise RepositoryError("not_regular", "parent is not a directory")
            if not target.resolve(strict=True).is_relative_to(root):
                raise RepositoryError("path_escape", "target is outside the canonical root")
            if not stat.S_ISREG(mode):
                raise RepositoryError("not_regular", "text reads require a regular file")
        except FileNotFoundError as exc:
            raise RepositoryError("missing", "requested file does not exist") from exc
        except (OSError, RuntimeError) as exc:
            raise RepositoryError("unreadable", "cannot access requested file") from exc
        return target

    def inventory(self, *, max_entries: int = DEFAULT_MAX_ENTRIES,
                  max_depth: int | None = DEFAULT_MAX_DEPTH,
                  max_nodes: int = DEFAULT_MAX_NODES) -> RepositoryInventory:
        """Sorted depth-first prefix; max_nodes includes excluded/unsupported visits."""
        _positive(max_entries, "max_entries")
        _positive(max_nodes, "max_nodes")
        if max_depth is not None:
            _positive(max_depth, "max_depth")
        root = self._require_root()
        entries, warnings = [], set()
        excluded = unsupported = unreadable = visited = 0
        truncated = False

        def children(directory: Path, limit: int):
            nonlocal unreadable
            try:
                with os.scandir(directory) as iterator:
                    return iter(heapq.nsmallest(limit, iterator, key=lambda entry: entry.name))
            except OSError:
                unreadable += 1
                warnings.add("unreadable_entry")
                return iter(())

        stack = [(children(root, max_nodes + 1), 1)]
        while stack:
            iterator, depth = stack[-1]
            entry = next(iterator, None)
            if entry is None:
                stack.pop()
                continue
            if visited >= max_nodes:
                truncated = True
                warnings.add("node_limit")
                break
            visited += 1
            path = Path(entry.path).relative_to(root).as_posix()
            if _blocked(path):
                excluded += 1
                continue
            try:
                _path_parts(path)
                info = entry.stat(follow_symlinks=False)
            except RepositoryError:
                unsupported += 1
                warnings.add("unsupported_path")
                continue
            except OSError:
                unreadable += 1
                warnings.add("unreadable_entry")
                continue
            mode = info.st_mode
            if stat.S_ISDIR(mode) and entry.name.casefold() in CACHE_DIRS:
                excluded += 1
                continue
            if stat.S_ISREG(mode):
                kind, size = RepositoryEntryKind.FILE, info.st_size
            elif stat.S_ISDIR(mode):
                kind, size = RepositoryEntryKind.DIRECTORY, None
            elif stat.S_ISLNK(mode):
                kind, size = RepositoryEntryKind.SYMLINK, None
            else:
                unsupported += 1
                warnings.add("unsupported_entry")
                continue
            if len(entries) >= max_entries:
                truncated = True
                warnings.add("entry_limit")
                break
            entries.append(RepositoryEntry(path=path, kind=kind, size_bytes=size))
            if kind == RepositoryEntryKind.DIRECTORY:
                if max_depth is not None and depth >= max_depth:
                    truncated = True
                    warnings.add("depth_limit")
                else:
                    stack.append((children(Path(entry.path), max_nodes - visited + 1), depth + 1))
        if excluded:
            warnings.add("excluded_policy")
        return RepositoryInventory(entries=entries, truncated=truncated,
                                   excluded_policy_count=excluded, unsupported_entry_count=unsupported,
                                   unreadable_entry_count=unreadable, warnings=sorted(warnings))

    def read_text(self, path: str, *, start_line: int = 1, max_lines: int = 100,
                  max_bytes: int = DEFAULT_READ_BYTES,
                  max_scan_bytes: int = DEFAULT_SCAN_BYTES) -> RepositoryText:
        """Bounded prefix scan and excerpt. Invalid unobserved suffixes are unknown."""
        for value, name in ((start_line, "start_line"), (max_lines, "max_lines"),
                            (max_bytes, "max_bytes"), (max_scan_bytes, "max_scan_bytes")):
            _positive(value, name)
        target = self._file(path)
        try:
            with target.open("rb") as stream:
                raw = stream.read(max_scan_bytes + 1)
        except OSError as exc:
            raise RepositoryError("unreadable", "cannot read requested file") from exc
        more = len(raw) > max_scan_bytes
        try:
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            text = decoder.decode(raw[:max_scan_bytes], final=not more)
        except UnicodeDecodeError as exc:
            raise RepositoryError("invalid_utf8", "observed source bytes are not UTF-8") from exc
        source_lines = _lines(text)
        if start_line > 1 and start_line > len(source_lines) and more:
            raise RepositoryError("scan_limit", "requested line lies beyond the bounded scan")
        selected = source_lines[start_line - 1:start_line - 1 + max_lines]
        selected_bytes = "".join(selected).encode("utf-8")
        content = _utf8_prefix(selected_bytes, max_bytes)
        truncated = more or len(source_lines) > start_line - 1 + max_lines or len(selected_bytes) > max_bytes
        represented = len(_lines(content))
        return RepositoryText(path=path, content=content,
                              start_line=start_line if represented else None,
                              end_line=start_line + represented - 1 if represented else None,
                              truncated=truncated)


def _config_name(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].casefold()
    return name in {"pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini", "noxfile.py"} or (
        name.startswith("requirements") and name.endswith(".txt")
    )


def _dependency(value: str) -> str | None:
    match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s*(?:\[|[<=>!~;@]|$))", value)
    return re.sub(r"[-_.]+", "-", match[1]).casefold() if match else None


def analyze_repository(view: RepositoryView, *, max_entries: int = DEFAULT_MAX_ENTRIES,
                       max_depth: int | None = DEFAULT_MAX_DEPTH, max_config_bytes: int = 131072,
                       max_config_files: int = 64, max_important_dirs: int = 8) -> RepositoryAnalysisResult:
    """Data-only configuration parsing and observable Python-MVP characterization."""
    if type(view) is not RepositoryView:
        raise RepositoryError("invalid_root", "analysis requires a RepositoryView")
    for value, name in ((max_config_bytes, "max_config_bytes"), (max_config_files, "max_config_files"),
                        (max_important_dirs, "max_important_dirs")):
        _positive(value, name)
    inventory = view.inventory(max_entries=max_entries, max_depth=max_depth)
    files = sorted(entry.path for entry in inventory.entries if entry.kind == RepositoryEntryKind.FILE)
    configs = [path for path in files if _config_name(path)]
    tests = [path for path in files if path.endswith(".py") and (
        any(part.casefold() in ("test", "tests") for part in path.split("/")[:-1])
        or path.rsplit("/", 1)[-1].startswith("test_") or path.endswith("_test.py"))]
    warnings = set(inventory.warnings)
    evidence = []
    dependency_paths: dict[str, set[str]] = {}
    pytest_configs = set()
    truncated = inventory.truncated or len(configs) > max_config_files
    if len(configs) > max_config_files:
        warnings.add("config_limit")
    for path in configs[:max_config_files]:
        name = path.rsplit("/", 1)[-1].casefold()
        if name in ("setup.py", "noxfile.py"):
            continue
        try:
            read = view.read_text(path, max_lines=max_config_bytes, max_bytes=max_config_bytes,
                                  max_scan_bytes=max_config_bytes)
            if read.truncated:
                warnings.add("config_too_large")
                truncated = True
                continue
            requirements = []
            if name == "pyproject.toml":
                data = tomllib.loads(read.content)
                project = data.get("project", {})
                if isinstance(project, dict):
                    declared = project.get("dependencies", [])
                    if isinstance(declared, list):
                        requirements.extend(item for item in declared if isinstance(item, str))
                        if any(not isinstance(item, str) for item in declared):
                            warnings.add("invalid_dependency_data")
                    else:
                        warnings.add("invalid_dependency_data")
                    optional = project.get("optional-dependencies", {})
                    if isinstance(optional, dict):
                        for values in optional.values():
                            if isinstance(values, list):
                                requirements.extend(item for item in values if isinstance(item, str))
                                if any(not isinstance(item, str) for item in values):
                                    warnings.add("invalid_dependency_data")
                            else:
                                warnings.add("invalid_dependency_data")
                    else:
                        warnings.add("invalid_dependency_data")
                else:
                    warnings.add("invalid_dependency_data")
                tool = data.get("tool", {})
                if isinstance(tool, dict):
                    if isinstance(tool.get("pytest"), dict):
                        pytest_configs.add(path)
                    poetry = tool.get("poetry", {})
                    if isinstance(poetry, dict):
                        tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
                        groups = poetry.get("group", {})
                        if isinstance(groups, dict):
                            tables.extend(group.get("dependencies", {}) for group in groups.values() if isinstance(group, dict))
                        for table in tables:
                            if isinstance(table, dict):
                                for key, constraint in table.items():
                                    if key == "python":
                                        continue
                                    if isinstance(constraint, (str, dict)):
                                        requirements.append(key)
                                    else:
                                        warnings.add("invalid_dependency_data")
            elif name.startswith("requirements"):
                requirements = read.content.splitlines()
            else:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(read.content)
                if parser.has_section("pytest") or parser.has_section("tool:pytest"):
                    pytest_configs.add(path)
                if parser.has_section("options"):
                    requirements.extend(parser.get("options", "install_requires", fallback="").splitlines())
                if parser.has_section("options.extras_require"):
                    for _, value in parser.items("options.extras_require"):
                        requirements.extend(value.splitlines())
            for value in requirements:
                package = _dependency(value)
                if package:
                    dependency_paths.setdefault(package, set()).add(path)
        except RepositoryError as exc:
            warnings.add(exc.code)
        except (tomllib.TOMLDecodeError, configparser.Error):
            warnings.add("malformed_config")
    python_paths = [path for path in files if path.endswith(".py") or _config_name(path) and (
        path.rsplit("/", 1)[-1].casefold() in ("pyproject.toml", "setup.cfg")
        or path.rsplit("/", 1)[-1].casefold().startswith("requirements"))]
    language = "python" if python_paths else None
    if language:
        evidence.append(RepositoryFactEvidence(field="language", value="python", path=python_paths[0],
                                                reason="observed Python source or project marker"))
    else:
        warnings.add("python_not_detected")
    frameworks = sorted(set(dependency_paths).intersection({"fastapi", "django", "flask"}))
    framework = frameworks[0] if len(frameworks) == 1 else None
    if len(frameworks) > 1:
        warnings.add("ambiguous_framework")
    for value in frameworks:
        for path in sorted(dependency_paths[value]):
            evidence.append(RepositoryFactEvidence(field="framework", value=value, path=path,
                                                    reason="declared direct dependency"))
    pytest_paths = dependency_paths.get("pytest", set())
    for path in sorted(pytest_paths | pytest_configs):
        evidence.append(RepositoryFactEvidence(field="test_framework", value="pytest", path=path,
                                                reason="declared direct dependency" if path in pytest_paths else "pytest configuration"))
    dirs = {path.split("/", 1)[0] for path in files if "/" in path and path.endswith(".py")}
    priority = {"src": 0, "app": 1, "lib": 2, "tests": 3, "test": 4}
    important = sorted(dirs, key=lambda path: (priority.get(path.casefold(), 5), path))
    if len(important) > max_important_dirs:
        warnings.add("important_dirs_limit")
        truncated = True
    important = important[:max_important_dirs]
    for path in important:
        evidence.append(RepositoryFactEvidence(field="important_dirs", value=path, path=path,
                                                reason="top-level directory contains eligible Python source"))
    readmes = [path for path in files if "/" not in path and path.casefold() in ("readme.md", "readme.rst", "readme.txt", "readme")]
    precedence = {"readme.md": 0, "readme.rst": 1, "readme.txt": 2, "readme": 3}
    readmes.sort(key=lambda path: (precedence[path.casefold()], path))
    excerpt = None
    if len(readmes) > 1:
        warnings.add("multiple_readmes")
    if readmes:
        try:
            excerpt = view.read_text(readmes[0])
            truncated |= excerpt.truncated
        except RepositoryError as exc:
            warnings.add(exc.code)
    return RepositoryAnalysisResult(profile=RepoProfile(language=language, framework=framework,
        test_framework="pytest" if pytest_paths or pytest_configs else None, important_dirs=important),
        inventory=inventory, test_files=tests, config_files=configs, readme_excerpt=excerpt,
        evidence=evidence, warnings=sorted(warnings), truncated=truncated)
