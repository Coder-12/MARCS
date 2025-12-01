# tests/test_symbolic_eval.py
from services.symbolic_eval import analyze_code_blob

def test_detect_eval_exec():
    code = "x = 1\nexec('print(1)')\n"
    findings = analyze_code_blob(code)
    ids = [f.id for f in findings]
    assert any("danger-eval" in fid or "danger-exec" in fid or "danger-" in fid for fid in ids)

def test_syntax_error_reported():
    code = "def foo(:\n  pass"
    findings = analyze_code_blob(code)
    assert any("syntax-error" == f.id for f in findings)