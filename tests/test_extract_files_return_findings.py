import pytest
from tests.test_file_extractor_hardening import DummyEvent
from services.file_extractor import extract_files_from_event

@pytest.mark.asyncio
async def test_return_findings_direct_payload():
    evt = DummyEvent(
        repo_full_name="me/repo",
        payload={"files": {"a.py": "KEY=AKIAABCDEFGHIJKLMNO12345"}}
    )

    out = await extract_files_from_event(evt, return_findings=True)

    assert "files" in out
    assert "findings" in out
    assert "a.py" in out["files"]

    sanitized = out["files"]["a.py"]
    assert "AKIA" not in sanitized
    assert "a.py" in out["findings"]
    assert len(out["findings"]["a.py"]) >= 1