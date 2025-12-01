# services/file_extractor.py
from __future__ import annotations
import os
import asyncio
import random
from typing import Dict, Any, Optional

import aiohttp

from core.logging import get_logger
logger = get_logger("services.file_extractor")

# import scrubber
from sanitizers.secret_scrubber import scrub_files  # Option A: we only return sanitized files
from sanitizers.large_file_filter import filter_files

GITHUB_RAW = "https://raw.githubusercontent.com"
LOCAL_REPO_ROOT_ENV = "MACRS_LOCAL_REPO_ROOT"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

# Hardening / safety defaults
FETCH_RETRIES = int(os.environ.get("MACRS_HTTP_FETCH_RETRIES", "3"))
FETCH_BACKOFF_BASE = float(os.environ.get("MACRS_HTTP_FETCH_BACKOFF", "0.5"))  # seconds
FETCH_TIMEOUT = float(os.environ.get("MACRS_HTTP_FETCH_TIMEOUT", "10"))  # seconds
MAX_FILE_CHARS = int(os.environ.get("MACRS_MAX_FILE_CHARS", "20000"))  # trim long files


async def _sleep_with_jitter(base: float, attempt: int) -> None:
    # exponential backoff with jitter
    wait = base * (2 ** (attempt - 1))
    # jitter 0.5x - 1.5x
    jitter = random.uniform(0.5, 1.5)
    await asyncio.sleep(wait * jitter)


async def fetch_http(url: str,
                     headers: Optional[Dict[str, str]] = None,
                     retries: int = FETCH_RETRIES,
                     timeout: float = FETCH_TIMEOUT) -> Optional[str]:
    """
    Fetch a URL with retries, exponential backoff and jitter.
    Returns text or None on persistent failure.
    """
    headers = headers or {}
    attempt = 0
    while attempt < retries:
        attempt += 1
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_obj) as sess:
                async with sess.get(url, headers=headers) as r:
                    if r.status == 200:
                        text = await r.text()
                        # If extremely large file, truncate to MAX_FILE_CHARS (we prefer partial than nothing)
                        if text and len(text) > MAX_FILE_CHARS:
                            logger.warning("fetch_http_truncated",
                                           url=url,
                                           original_len=len(text),
                                           truncated_to=MAX_FILE_CHARS)
                            return text[:MAX_FILE_CHARS] + "\n/* TRUNCATED */"
                        return text
                    else:
                        logger.warning("fetch_http_non200",
                                       url=url, status=r.status, attempt=attempt)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("fetch_http_error",
                           url=url, attempt=attempt, error=str(e))
        # backoff before next try (unless last attempt)
        if attempt < retries:
            await _sleep_with_jitter(FETCH_BACKOFF_BASE, attempt)
    logger.error("fetch_http_failed", url=url, retries=retries)
    return None

async def load_local_file(path: str) -> Optional[str]:
    """
    Load file from disk. If too large, truncate to MAX_FILE_CHARS.
    Returns None on any IO error.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            txt = fh.read()
            if txt and len(txt) > MAX_FILE_CHARS:
                logger.warning("local_file_truncated",
                               path=path,
                               original_len=len(txt),
                               truncated_to=MAX_FILE_CHARS)
                return txt[:MAX_FILE_CHARS] + "\n/* TRUNCATED */"
            return txt
    except Exception as e:
        logger.warning("local_file_read_failed", path=path, error=str(e))
        return None


async def extract_files_from_event(evt,
                                   return_findings: bool = False) -> Dict[str, str] | Dict[str, Any]:
    """
    Main extraction entry.
    Option A behavior:
        If return_findings=False:
            return { filename: sanitized_text }
        If return_findings=True:
            return { "files": {…}, "findings": {…} }
    """
    logger.debug("file_extractor: local_root=%s",
                 os.environ.get("MACRS_LOCAL_REPO_ROOT"))

    payload = evt.payload if isinstance(evt.payload, dict) else {}
    files = payload.get("files")

    # ---------------------------------------------------------
    # 1) Direct files passed (unit tests & synthetic events)
    # ---------------------------------------------------------
    if isinstance(files, dict) and files:
        logger.debug("file_extractor: using files directly from payload")
        # apply truncation/sanitation policy to values
        out_raw: Dict[str, str] = {}
        for k, v in files.items():
            try:
                if v is None:
                    continue
                s = v
                if len(s) > MAX_FILE_CHARS:
                    logger.warning("payload_file_truncated",
                                   file=k,
                                   original_len=len(s),
                                   truncated_to=MAX_FILE_CHARS)
                    s = s[:MAX_FILE_CHARS] + "\n/* TRUNCATED */"
                out_raw[k] = s
            except Exception:
                continue

        filtered, large_meta = filter_files(
            out_raw,
            max_chars=int(os.environ.get("MACRS_MAX_FILE_CHARS", 20000)),
            truncate_to=int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", 5000)),
        )
        sanitized, findings = scrub_files(filtered)

        if findings:
            logger.info("file_extractor_scrub_findings",
                        files=len(findings),
                        details={k: len(v) for k, v in findings.items()})
            logger.info("file_extractor_largefile_meta",
                        files=len(large_meta),
                        details={k: v["reason"] for k, v in large_meta.items()})
        return {"files": sanitized,
                "findings": findings,
                "large_files": large_meta,} if return_findings else sanitized

    repo = getattr(evt, "repo_full_name", None)
    if not repo:
        return {} if not return_findings else {"files": {}, "findings": {}, "large_files": {}}

    local_root = os.environ.get(LOCAL_REPO_ROOT_ENV)
    github_token = os.environ.get(GITHUB_TOKEN_ENV)
    headers = {"Authorization": f"Bearer {github_token}"} if github_token else {}

    # ---------------------------------------------------------
    # 2) GitHub push payload format (commits list)
    # ---------------------------------------------------------
    commits = payload.get("commits") or []
    if commits:
        results_raw: Dict[str, str] = {}
        changed: Dict[str, None] = {}

        for c in commits:
            for f in c.get("added", []):
                changed[f] = None
            for f in c.get("modified", []):
                changed[f] = None

        if not changed:
            clean = {}  # sanitized empty
            logger.debug("file_extractor: push event had no changed files")
            return {"files": clean, "findings": {}, "large_files": {}} if return_findings else clean

        default_branch = payload.get("ref", "").split("/")[-1] or "main" # type: ignore[arg-type]

        for f in changed.keys():
            # 2A) Try local repo clone
            if local_root:
                local_path = os.path.join(local_root, repo, f)
                if os.path.isfile(local_path):  # <— critical condition
                    txt = await load_local_file(local_path)
                    if txt is not None:
                        results_raw[f] = txt
                        continue

            # 2B) Try GitHub raw
            url = f"{GITHUB_RAW}/{repo}/{default_branch}/{f}"
            # always reference via module to ensure monkeypatch works
            txt = None
            for _attempt in range(FETCH_RETRIES): # type: ignore[arg-type]
                txt = await fetch_http(
                    url,
                    headers=headers,
                    retries=FETCH_RETRIES,  # forward for API consistency
                    timeout=FETCH_TIMEOUT,
                )
                if txt is not None:
                    results_raw[f] = txt
                    break

            if txt is None:
                logger.warning("file_extractor_fetch_failed",
                               repo=repo, file=f, url=url)

        # sanitize before returning (Option A)
        filtered, large_meta = filter_files(
            results_raw,
            max_chars=int(os.environ.get("MACRS_MAX_FILE_CHARS", 20000)),
            truncate_to=int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", 5000)),
        )
        sanitized, findings = scrub_files(filtered)

        if findings:
            logger.info("file_extractor_scrub_findings",
                        files=len(findings),
                        details={k: len(v) for k, v in findings.items()})
            logger.info("file_extractor_largefile_meta",
                        files=len(large_meta),
                        details={k: v["reason"] for k, v in large_meta.items()})
        return {"files": sanitized,
                "findings": findings,
                "large_files": large_meta,} if return_findings else sanitized

    # ---------------------------------------------------------
    # 3) Local repo fallback (full scan)
    # Enabled when:
    #   - MACRS_LOCAL_REPO_ROOT is set
    #   - AND (payload is empty OR payload["full_scan"] is True)
    #   - AND payload["full_scan"] is not False
    # ---------------------------------------------------------
    should_full_scan = False

    if local_root:
        root = os.path.join(local_root, repo)

        if payload.get("full_scan") is True:
            should_full_scan = True

        elif payload.get("full_scan") is False:
            should_full_scan = False

        elif len(payload) == 0:
            if os.path.isdir(root):
                should_full_scan = True
            else:
                should_full_scan = False
        else:
            should_full_scan = False

    if local_root and should_full_scan:
        results_raw: Dict[str, str] = {}
        root = os.path.join(local_root, repo)
        if not os.path.isdir(root):
            logger.warning("file_extractor_local_root_missing",
                           root=root, repo=repo)
            return {"files": {}, "findings": {}, "large_files": {}} if return_findings else {}

        for base, _dirs, filenames in os.walk(root):
            for fn in filenames:
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, root)
                txt = await load_local_file(full)
                if txt:
                    results_raw[rel] = txt

        filtered, large_meta = filter_files(
            results_raw,
            max_chars=int(os.environ.get("MACRS_MAX_FILE_CHARS", 20000)),
            truncate_to=int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", 5000)),
        )
        sanitized, findings = scrub_files(filtered)

        if findings:
            logger.info("file_extractor_scrub_findings",
                        files=len(findings),
                        details={k: len(v) for k, v in findings.items()})
            logger.info("file_extractor_largefile_meta",
                        files=len(large_meta),
                        details={k: v["reason"] for k, v in large_meta.items()})
        return {"files": sanitized,
                "findings": findings,
                "large_files": large_meta,} if return_findings else sanitized

    # ---------------------------------------------------------
    # 4) Nothing else matched → return empty dict
    # ---------------------------------------------------------
    logger.debug("file_extractor: no files found for event", repo=repo)
    empty = {}
    return {"files": empty, "findings": empty, "large_files": empty} if return_findings else empty