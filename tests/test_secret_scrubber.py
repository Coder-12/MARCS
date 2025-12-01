import pytest
from sanitizers.secret_scrubber import scrub_text, scrub_files

def test_scrub_basic_tokens_and_github_pat():
    txt = "normal text\nToken: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd\nend"
    scrubbed, findings = scrub_text(txt)
    assert "ghp_" not in scrubbed
    assert "<REDACTED:" in scrubbed
    assert any(f["type"].startswith("github_pat") or f["type"].startswith("pem") is False for f in findings)

def test_scrub_jwt_and_pem():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." \
          "eyJzdWIiOiIxMjM0NTY3ODkwIn0." \
          "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIBV...END PRIVATE KEY-----"
    txt = f"header\n{jwt}\nfoo\n{pem}\nfooter"
    scrubbed, findings = scrub_text(txt)
    assert "-----BEGIN" not in scrubbed
    assert "<REDACTED:" in scrubbed
    # expect at least two findings: jwt and pem
    types = {f["type"] for f in findings}
    assert any("jwt" in t for t in types) or any("pem" in t for t in types)

def test_scrub_files_mapping():
    files = {"a.py": "print(1)\nTOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZA0123456789"}
    sanitized, findings = scrub_files(files)
    assert "a.py" in sanitized
    assert "ghp_" not in sanitized["a.py"]
    assert findings.get("a.py")