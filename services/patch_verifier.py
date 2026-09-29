# services/patch_verifier.py
import os
import shutil
import subprocess
import tempfile
import re
from typing import Tuple, Optional, Dict, List

UNIFIED_DIFF_HDR_RE = re.compile(r"^(diff --git |--- |\+\+\+ |@@ )", re.MULTILINE)


def looks_like_unified_diff(patch_text: str) -> bool:
    """
    Quick heuristic: unified diff typically contains lines starting with:
      diff --git a/... b/...
      --- a/...
      +++ b/...
      @@ -l,s +l,s @@
    """
    if not patch_text or not isinstance(patch_text, str):
        return False
    return bool(UNIFIED_DIFF_HDR_RE.search(patch_text))


def _run_git_apply_check(patch_text: str, repo_path: str) -> Tuple[bool, str]:
    """
    Try to run `git apply --check` on the patch against repo_path.
    Returns (ok, output).
    If git is not available or repo_path doesn't look like a git repo, raises RuntimeError.
    """
    if not shutil.which("git"):
        raise RuntimeError("git not found in PATH")
    if not os.path.isdir(repo_path):
        raise RuntimeError(f"repo_path '{repo_path}' not found")
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise RuntimeError(f"repo_path '{repo_path}' is not a git repository (no .git)")

    proc = subprocess.Popen(
        ["git", "apply", "--check", "--unsafe-paths", "-"],
        cwd=repo_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = proc.communicate(patch_text)
    ok = proc.returncode == 0
    return ok, out + err


def _apply_patch_to_temp_and_check_syntax(
    patch_text: str, repo_path: Optional[str], python_executable: str = "python"
) -> Tuple[bool, str]:
    """
    Apply patch into a copied temporary folder (so real repo is untouched) and run syntax checks
    for .py files touched by the patch. Returns (ok, message).
    Uses 'git apply' if available (recommended), otherwise tries a naive apply using python patch parsing.
    """
    tmpdir = tempfile.mkdtemp(prefix="patch-check-")
    try:
        # If repo_path provided and exists, copy it to tmpdir
        if repo_path and os.path.isdir(repo_path):
            # copy tree (exclude .git)
            def _ignore(src, names):
                return {".git"} if ".git" in names else set()

            shutil.copytree(repo_path, os.path.join(tmpdir, "repo"), dirs_exist_ok=True, ignore=_ignore)
            workdir = os.path.join(tmpdir, "repo")
        else:
            # create empty repo directory
            workdir = os.path.join(tmpdir, "repo")
            os.makedirs(workdir, exist_ok=True)

        # try git apply in workdir if git present; initialize a repo if needed
        if shutil.which("git"):
            # ensure repo has .git so git apply works
            if not os.path.isdir(os.path.join(workdir, ".git")):
                subprocess.run(["git", "init"], cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # make initial commit so apply has context
                open(os.path.join(workdir, ".gitkeep"), "w").close()
                subprocess.run(["git", "add", "."], cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["git", "commit", "-m", "init"], cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            proc = subprocess.Popen(
                ["git", "apply", "--index", "-"],
                cwd=workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = proc.communicate(patch_text)
            if proc.returncode != 0:
                return False, out + err
        else:
            # minimal fallback: we will attempt to parse the diff and write new files/patch hunks.
            # This is intentionally conservative: if we cannot apply cleanly, return False.
            # For Phase-0 this will be rarely used; prefer `git`.
            from io import StringIO

            # super-simple patch application: find file headers and replace file content when `+++` present.
            # This is NOT a full patch implementation. It attempts to detect new file content for simple cases.
            fp = StringIO(patch_text)
            current_file = None
            new_content_lines: List[str] = []
            writing = False
            for line in fp:
                if line.startswith("+++ "):
                    # extract path after +++
                    path = line[4:].strip()
                    # unify form like "b/path" or "/dev/null"
                    if path.endswith("/dev/null") or path.endswith("null"):
                        current_file = None
                        writing = False
                    else:
                        # strip possible a/ b/ prefix
                        if path.startswith("b/") or path.startswith("a/"):
                            path = path[2:]
                        current_file = os.path.join(workdir, path)
                        # ensure parent dir
                        os.makedirs(os.path.dirname(current_file), exist_ok=True)
                        new_content_lines = []
                        writing = True
                elif writing:
                    if line.startswith("@@"):
                        # hunk header — continue
                        continue
                    elif line.startswith("+") and not line.startswith("+++"):
                        new_content_lines.append(line[1:])
                    elif line.startswith("-"):
                        # removed line — ignore in this simplistic reconstruction
                        continue
                    else:
                        # context line
                        if line.startswith(" "):
                            new_content_lines.append(line[1:])
                        # when we detect next file header we flush; simplified
                # note: this fallback is intentionally limited

            # flush any file we collected
            if current_file and new_content_lines:
                with open(current_file, "w", encoding="utf-8") as f:
                    f.writelines(new_content_lines)

        # now run python syntax checks for any .py files changed in the tmp workdir
        py_files = []
        for root, _, files in os.walk(workdir):
            for fn in files:
                if fn.endswith(".py"):
                    py_files.append(os.path.join(root, fn))

        # run compile on all .py files (cheap and deterministic)
        for p in py_files:
            proc = subprocess.Popen([python_executable, "-m", "py_compile", p], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, err = proc.communicate()
            if proc.returncode != 0:
                return False, f"Python syntax error in file {p}: {err.strip()}"

        return True, "ok"
    finally:
        shutil.rmtree(tmpdir)


def verify_patch(
    patch_text: str,
    repo_path: Optional[str] = None,
    python_executable: str = "python"
) -> Dict[str, str]:
    """
    Verify patch for format and deterministic apply/syntax checks.

    Returns a dict with fields:
      - ok: "true" / "false"
      - reason: human-friendly reason when false
      - check: which method used ("git-check", "py-temp-apply", "heuristic")
    """

    # 0. Allow Phase-0 stub patches
    if patch_text.strip().startswith("# PATCH_STUB_"):
        return {
            "ok": "true",
            "check": "stub_patch_bypass",
            "reason": "phase0_stub_patch"
        }

    if not looks_like_unified_diff(patch_text):
        return {"ok": "false", "reason": "not_unified_diff", "check": "heuristic"}

    # Try fast git check if possible and repo provided
    if repo_path:
        try:
            ok, output = _run_git_apply_check(patch_text, repo_path)
            if ok:
                return {"ok": "true", "reason": "git_apply_check_ok", "check": "git-check"}
            else:
                # fallthrough to temp-apply to get more details & syntax checks
                pass
        except Exception:
            # ignore and continue to temp apply fallback
            pass

    # Fallback: apply to temp copy and run python syntax checks (if any python files modified)
    try:
        ok, output = _apply_patch_to_temp_and_check_syntax(patch_text, repo_path, python_executable)
        if ok:
            return {"ok": "true", "reason": "temp_apply_ok", "check": "temp-apply"}
        else:
            return {"ok": "false", "reason": "temp_apply_failed: " + (output or "unknown"), "check": "temp-apply"}
    except Exception as exc:
        return {"ok": "false", "reason": f"exception_during_check: {exc}", "check": "temp-apply"}