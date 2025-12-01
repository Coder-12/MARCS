import json
import pytest

from sanitizers.llm_output_clean import rescue_json


# ---------------------------------------------------------------------
# 1) Fenced block stripping
# ---------------------------------------------------------------------
def test_strip_fences():
    raw = """Here is JSON:
            { \"a\": 1, }
          """
    out = rescue_json(raw)
    # Backticks should be gone
    assert "```" not in out
    # JSON inner content must remain
    assert "{ \"a\": 1 }" in out


# ---------------------------------------------------------------------
# 2) Extract JSON-like region
# ---------------------------------------------------------------------
def test_extract_json_like():
    raw = "blah blah {\"x\":1, \"y\":2} trailing noise"
    out = rescue_json(raw)
    assert out == "{\"x\":1, \"y\":2}"


# ---------------------------------------------------------------------
# 3) Remove trailing commas
# ---------------------------------------------------------------------
def test_remove_trailing_commas_object():
    raw = "{ \"a\": 1, }"
    out = rescue_json(raw)
    parsed = json.loads(out)
    assert parsed == {"a": 1}


def test_remove_trailing_commas_array():
    raw = "[1, 2, 3,]"
    out = rescue_json(raw)
    parsed = json.loads(out)
    assert parsed == [1, 2, 3]


# ---------------------------------------------------------------------
# 4) Fix unclosed quotes
# ---------------------------------------------------------------------
def test_fix_unclosed_quotes():
    raw = '{ "a": "unterminated }'
    out = rescue_json(raw)
    # Should now parse as valid JSON
    parsed = json.loads(out)
    assert parsed["a"].startswith("unterminated")


# ---------------------------------------------------------------------
# 5) Balance braces
# ---------------------------------------------------------------------
def test_balance_braces_missing_closer():
    raw = '{ "a": 1'
    out = rescue_json(raw)
    parsed = json.loads(out)
    assert parsed == {"a": 1}


def test_balance_braces_extra_closer():
    raw = '{ "a": 1 }}'
    out = rescue_json(raw)
    parsed = json.loads(out)
    assert parsed == {"a": 1}


# ---------------------------------------------------------------------
# 6) End-to-end rescue
# ---------------------------------------------------------------------
def test_parse_json_rescue_full_mess():
    raw = """
        #### CODE REVIEW ####
        ```json
        {
          "a": 1,
          "b": 2,
        }
        ```
        trailing commentary...
        """

    out = rescue_json(raw)
    parsed = json.loads(out)
    assert parsed == {"a": 1, "b": 2}


# ---------------------------------------------------------------------
# 7) Array-only JSON (LLM sometimes returns arrays)
# ---------------------------------------------------------------------
def test_extract_json_array_only():
    raw = "noise [ {\"a\":1}, {\"b\":2}, ] more noise"
    out = rescue_json(raw)
    parsed = json.loads(out)
    assert parsed == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------
# 8) Empty or None-safe
# ---------------------------------------------------------------------
def test_none_safe():
    assert rescue_json(None) == ""


def test_empty_string_safe():
    assert rescue_json("") == ""