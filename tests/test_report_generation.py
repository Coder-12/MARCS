# tests/test_report_generation.py
import json
import os
import tempfile
from core.report import generate_html_report

def test_generate_html_report(tmp_path):
    event_id = "evt-test-report"
    artifact_dir = str(tmp_path / "artifacts")
    review = {
        "findings": ["missing docstring in foo()"],
        "suggested_patches": {
            "foo.py": "--- a/foo.py\n+++ b/foo.py\n@@ -1,0 +1,2 @@\n+def foo():\n+    pass\n"
        }
    }
    apply_result = {
        "applied": {"foo.py": "def foo():\n    pass\n"},
        "dropped": {},
        "backups": {"foo.py": "backups/evt-test-report/foo.py.bak"},
        "journal": "journals/evt-test-report.jsonl",
        "dry_run": False
    }

    report_path = generate_html_report(event_id, review, apply_result, artifact_dir)
    assert os.path.exists(report_path)

    # check that key sections are present
    content = open(report_path, "r", encoding="utf-8").read()
    assert "MARCS Report" in content
    assert "missing docstring" in content
    assert "foo.py" in content
    assert "applied" in content.lower()
    assert "backups/evt-test-report/foo.py.bak" in content