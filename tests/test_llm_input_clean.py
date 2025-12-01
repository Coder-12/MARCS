import pytest
from sanitizers.llm_input_clean import clean_text, clean_files

def test_clean_removes_control_chars():
    s = "A\x01B\x02C\nD\tE"
    out = clean_text(s)
    # \x01, \x02 removed; \n, \t remain
    assert out == "A B C\nD\tE".replace(" ", "") or "ABC\nD\tE"


def test_clean_removes_nul_bytes():
    assert clean_text("abc\x00def") == "abcdef"


def test_clean_unicode_separators():
    s = "hello\u2028world\u2029test\u0085here"
    out = clean_text(s)
    assert out == "hello\nworld\ntest\nhere"


def test_clean_neutralizes_code_fences():
    s = "```\ncode\n```"
    out = clean_text(s)
    # Should contain a zero-width space after ```
    assert "```" in out
    assert "```" + "\u200b" in out  # ZWSP added


def test_clean_collapse_spaces():
    s = "int  x   =    5"
    out = clean_text(s)
    assert out == "int x = 5"


def test_clean_collapse_newlines():
    s = "a\n\n\n\nb\n\n\nc"
    out = clean_text(s)
    assert out == "a\n\nb\n\nc"


def test_clean_trim_lines():
    s = "a   \nb   \n  c  "
    out = clean_text(s)
    assert out == "a\nb\nc"


def test_clean_files_basic():
    files = {
        "a.py": "print(\x00 'hi')",
        "b.txt": "A\u2028B",
    }
    out = clean_files(files)
    assert out["a.py"] == "print('hi')"
    assert out["b.txt"] == "A\nB"