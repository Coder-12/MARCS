# tests/test_orchestrator_safety.py
import os
from orchestration.orchestrator import DemoOrchestrator

def make_large_patch(lines=201):
    # produce a unified-style patch with many added lines
    header = "--- a/big.py\n+++ b/big.py\n"
    hunk = "@@ -1,0 +1,%d @@\n" % (lines)
    adds = "".join([f"+line_{i}\n" for i in range(lines)])
    return header + hunk + adds

def make_dangerous_patch():
    return """--- a/x.py
+++ b/x.py
@@ -0,0 +1,3 @@
+import os
+os.system("rm -rf /tmp/somefile")  # dangerous
+exec("print('hi')")
"""

def test_large_patch_warning():
    orch = DemoOrchestrator()
    p = make_large_patch(201)
    warnings = orch.run_safety_checks("big.py", p)
    assert "large_patch" in warnings

def test_dangerous_code_warning():
    orch = DemoOrchestrator()
    p = make_dangerous_patch()
    warnings = orch.run_safety_checks("x.py", p)
    # we expect both specific dangerous tags and the generic 'dangerous_code'
    assert any(w.startswith("dangerous_code") for w in warnings)
    assert "dangerous_code" in warnings or any(w.startswith("dangerous_code:") for w in warnings)

def test_sensitive_file_warning():
    orch = DemoOrchestrator()
    p = "--- a/requirements.txt\n+++ b/requirements.txt\n@@ -0,0 +1,1 @@\n+flask==1.2.3\n"
    warnings = orch.run_safety_checks("requirements.txt", p)
    assert "sensitive_file" in warnings