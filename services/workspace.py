"""Owned disposable copies, not process isolation or a security sandbox.

Copy regular files/directories and preserve links without following them. No
repository code is executed. Concurrent hostile filesystem mutation is outside
this boundary's threat model.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile


class WorkspaceError(Exception):
    """Creation, ownership, or liveness failure."""


def _copy_policy(directory: str, names: list[str]) -> set[str]:
    for name in sorted(names):
        if name == ".git":
            continue
        mode = (Path(directory) / name).lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            raise WorkspaceError(f"unsupported source filesystem entry: {name!r}")
    return {".git"} if ".git" in names else set()


class Workspace:
    """Construction owns its temp handle; callers cannot provide a cleanup path.

    Public roots are read-only. Cleanup uses the retained TemporaryDirectory,
    never a caller-selected workspace_root. Use dispose or a context manager.
    """

    __slots__ = ("__source_root", "__workspace_root", "__owner", "__disposed")

    def __init__(self, source_repo: str | os.PathLike[str]):
        try:
            source = Path(source_repo).resolve(strict=True)
            if not source.is_dir():
                raise WorkspaceError("source must be an existing directory")
            storage = Path(tempfile.gettempdir()).resolve(strict=True)
            if storage.is_relative_to(source):
                raise WorkspaceError("temporary storage must be outside the source tree")
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise WorkspaceError("cannot resolve source or temporary storage") from exc

        owner = None
        try:
            owner = tempfile.TemporaryDirectory(prefix="marcs-candidate-", dir=storage)
            candidate = Path(owner.name) / "repo"
            shutil.copytree(source, candidate, symlinks=True, ignore=_copy_policy)
        except Exception as exc:
            if owner is not None:
                try:
                    owner.cleanup()
                except OSError as cleanup_error:
                    raise WorkspaceError("candidate copy and owned cleanup failed") from cleanup_error
            if isinstance(exc, WorkspaceError):
                raise
            raise WorkspaceError("candidate copy failed") from exc

        self.__source_root = source
        self.__workspace_root = candidate
        self.__owner = owner
        self.__disposed = False

    @property
    def source_root(self) -> Path:
        return self.__source_root

    @property
    def workspace_root(self) -> Path:
        return self.__workspace_root

    def _require_live(self) -> Path:
        owner = getattr(self, "_Workspace__owner", None)
        if owner is None or self.__disposed:
            raise WorkspaceError("workspace is not live")
        owned = Path(owner.name)
        root = self.__workspace_root
        if root != owned / "repo":
            raise WorkspaceError("workspace root is not owned candidate storage")
        try:
            if owned.is_symlink() or root.is_symlink() or not root.is_dir():
                raise WorkspaceError("workspace is not live")
            if owned.resolve(strict=True) != owned or root.resolve(strict=True) != root:
                raise WorkspaceError("workspace ownership path changed")
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError("workspace is not live") from exc
        return root

    def dispose(self) -> None:
        owner = getattr(self, "_Workspace__owner", None)
        if owner is None:
            raise WorkspaceError("cleanup requires an owned Workspace")
        if self.__disposed:
            return
        try:
            owner.cleanup()
        except OSError as exc:
            raise WorkspaceError("owned workspace cleanup failed") from exc
        self.__disposed = True

    def __enter__(self) -> Workspace:
        self._require_live()
        return self

    def __exit__(self, *exc: object) -> None:
        self.dispose()


def create_workspace(source_repo: str | os.PathLike[str]) -> Workspace:
    return Workspace(source_repo)


def dispose_workspace(workspace: Workspace) -> None:
    if type(workspace) is not Workspace:
        raise WorkspaceError("cleanup requires an owned Workspace")
    workspace.dispose()
