import pytest
from sanitizers.large_file_filter import filter_files, _is_mostly_binary


def test_detect_binary_nul():
    # Contains NUL byte — should be detected as binary
    s = "hello\x00world"
    assert _is_mostly_binary(s) is True


def test_detect_binary_high_nonprint():
    # High-byte characters (>= 128)
    # This simulates typical binary/encoded data
    s = "".join(chr(200) for _ in range(5000))  # 100% high bytes
    assert _is_mostly_binary(s) is True

    # Also test mixed content
    s2 = "ABC" + "".join(chr(210) for _ in range(1000)) + "XYZ"
    assert _is_mostly_binary(s2) is True


@pytest.mark.asyncio
async def test_skip_binary_from_payload(monkeypatch):
    # Disable local repo so extractor uses payload path
    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")

    # Construct binary payload
    binary_content = "".join(chr(i % 256) for i in range(5000))

    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {"files": {"binfile.bin": binary_content}}

    from services.file_extractor import extract_files_from_event
    out = await extract_files_from_event(evt, return_findings=True)

    # Ensure metadata recorded correctly
    meta = out["large_files"]
    assert "binfile.bin" in meta
    assert meta["binfile.bin"]["skipped"] is True
    assert meta["binfile.bin"]["reason"] == "binary_content"

    # And ensure extractor does not include binary contents
    assert "binfile.bin" not in out["files"]