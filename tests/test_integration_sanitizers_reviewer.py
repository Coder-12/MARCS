import pytest
import json

from services.reviewer import Reviewer
from tests.test_reviewer_prompt_cap import MockLLM
from sanitizers.llm_input_clean import clean_text

@pytest.mark.asyncio
async def test_reviewer_integration_input_output_sanitization(monkeypatch):
    """
    Ensures:
      - input sanitization runs
      - output rescue fixes messy JSON
      - reviewer returns clean structured dict
    """
    # input contains:
    # - control chars
    # - unicode separators
    # - code fences
    # - excessive whitespace
    dirty_code = (
        "print('hi')" +
        "\x00" +
        "\u2028" +
        "```python\nprint('bad')\n```" +
        "   \n\n\n"
    )

    files = {"main.py": dirty_code}

    # force narrow prompt cap so small file always included
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "5000")

    # Mock LLM returns a messy response including:
    # - fences
    # - trailing commas
    # - commentary
    RAW = """
    Here is your JSON:
    ```json
    {
      "findings": [
        { "id": "x1", "category": "logic", "severity": "medium", "message": "ok", },
      ],
      "suggested_patches": {},
      "summary": "done",
    }
    ```
    EXTRA COMMENTARY
    """

    class DirtyMockLLM(MockLLM):
        async def chat_complete(self, *args, **kwargs):
            return {"choices": [{"message": {"content": RAW}}]}

    r = Reviewer(llm=DirtyMockLLM())
    out = await r.review_repo_files(
        event_id="EVT",
        repo="me/repo",
        files=files,
        context="Ctx\u2029"
    )

    # -------------------------------
    # Validate Reviewer Output
    # -------------------------------
    assert out["repo"] == "me/repo"
    assert isinstance(out["findings"], list)
    assert out["findings"][0]["id"] == "x1"
    assert out["summary"] == "done"

    # prompt cap metadata present
    assert "prompt_cap" in out["metadata"]
    # clean JSON extracted
    assert isinstance(out["suggested_patches"], dict)

    # NO fence remnants
    raw_json = json.dumps(out)
    assert "```" not in raw_json