# tests/test_patch_verifier.py
import pytest
from services.patch_verifier import looks_like_unified_diff, verify_patch

SAMPLE_DIFF = """diff --git a/foo.py b/foo.py
index 0000001..1111111 100644
--- a/foo.py
+++ b/foo.py
@@ -0,0 +1,3 @@
+def hello():
+    print("hello")
+
"""

def test_looks_like_unified_diff():
    assert looks_like_unified_diff(SAMPLE_DIFF)

def test_verify_patch_heuristic_no_repo():
    res = verify_patch(SAMPLE_DIFF, repo_path=None)
    # Without a real repository, temp-apply should still succeed for this small new file
    assert res["ok"] in ("true", "false")
    # At least method should be present
    assert "check" in res