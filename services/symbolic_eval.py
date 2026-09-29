# services/symbolic_eval.py
from __future__ import annotations
import ast
import subprocess
import shutil
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from agents.results import AgentFinding, AgentResult
from core.logging import get_logger
import os

logger = get_logger("services.symbolic_eval")

def _ast_find_dangerous_calls(code: str) -> List[Dict[str, Any]]:
    """AST-based rules: detect eval/exec/compile, subprocess usage, os.system, pickle.loads, etc."""
    findings = []
    try:
        tree = ast.parse(code)
    except Exception as e:
        # syntax errors are also useful to report
        findings.append({"id": "syntax-error", "severity": "high", "msg": f"Syntax error parsing code: {e}"})
        return findings

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            # function names that are risky
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                # e.g., os.system, subprocess.Popen
                name = f"{getattr(func.value, 'id', 'obj')}.{func.attr}"

            if name in ("eval", "exec", "compile", "pickle.loads", "os.system", "subprocess.Popen", "subprocess.call", "subprocess.run"):
                findings.append({
                    "id": f"danger-{name}",
                    "severity": "high",
                    "msg": f"Use of dangerous function `{name}` detected at line {getattr(node, 'lineno', '?')}",
                })
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import):
            for alias in node.names:
                if alias.name in ("pickle", "subprocess"):
                    findings.append({
                        "id": f"import-{alias.name}",
                        "severity": "medium",
                        "msg": f"Import of `{alias.name}` (line {node.lineno}) may be risky in untrusted code",
                    })
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom):
            module = node.module or ""
            if module.startswith("pickle") or module.startswith("subprocess"):
                findings.append({
                    "id": f"importfrom-{module}",
                    "severity": "medium",
                    "msg": f"Import from `{module}` may be risky (line {node.lineno})",
                })
            self.generic_visit(node)

    Visitor().visit(tree)
    return findings


def _simple_format_string_checks(code: str) -> List[Dict[str, Any]]:
    """Detect suspicious format strings that incorporate unescaped shell templates or format() usage with input."""
    findings = []
    if ".format(" in code:
        findings.append({
            "id": "format-usage",
            "severity": "low",
            "msg": "Usage of `.format(...)` found — ensure user input is not used to create shell commands.",
        })
    if f"f\"" in code or "f'" in code:
        # simplistic check for f-strings
        findings.append({
            "id": "fstring-usage",
            "severity": "low",
            "msg": "f-string usage detected — inspect for shell/command injection if values are untrusted.",
        })
    return findings


def _run_external_tool(tool_name: str, args: List[str], timeout: int = 5) -> Optional[str]:
    """Call an external tool (if installed) and return its stdout. Non-fatal on missing tool."""
    if shutil.which(tool_name) is None:
        return None
    try:
        out = subprocess.run([tool_name] + args, capture_output=True, text=True, timeout=timeout)
        return out.stdout + out.stderr
    except Exception as e:
        logger.warning("external_tool_failed", tool=tool_name, error=str(e))
        return None


def analyze_code_blob(code: str, filename: str = "<hunk>") -> List[AgentFinding]:
    """
    Analyze a code blob and return AgentFinding list.
    This is intentionally lightweight and deterministic.
    """
    findings_raw: List[Dict[str, Any]] = []
    findings_raw.extend(_ast_find_dangerous_calls(code))
    findings_raw.extend(_simple_format_string_checks(code))

    # Optionally run bandit (if available) for security hints
    bandit_out = _run_external_tool("bandit", ["-r", "-f", "json", "-q", filename]) if shutil.which("bandit") else None
    if bandit_out:
        # bandit usually expects paths; for hunk we might skip; if output, attempt to parse JSON
        try:
            parsed = json.loads(bandit_out)
            for item in (parsed.get("results") or []):
                findings_raw.append({
                    "id": f"bandit-{item.get('test_id')}",
                    "severity": "medium",
                    "msg": item.get("issue_text") or item.get("test_name"),
                })
        except Exception:
            pass

    # Convert to AgentFinding objects
    afinds: List[AgentFinding] = []
    for idx, fr in enumerate(findings_raw):
        fid = fr.get("id") or f"sym-{idx}"
        sev = fr.get("severity", "low")
        # map our severities -> allowed literal
        if sev not in ("low", "medium", "high"):
            sev = "low"
        afinds.append(
            AgentFinding(
                id=str(fid),
                severity=sev,
                message=str(fr.get("msg", "")),
                confidence=0.9 if sev == "high" else 0.6 if sev == "medium" else 0.3,
                category="security" if "danger" in fid or "bandit" in fid else "style",
                location=f"{filename}:{fr.get('lineno', '0')}" if fr.get("lineno") else filename,
                explanation=fr.get("msg"),
                metadata={"raw": fr},
            )
        )
    return afinds


def analyze_hunk(hunk: Dict[str, Any]) -> AgentResult:
    """
    Entry point for a single hunk (dict with keys: code, repo_full_name, delivery_id...).
    Returns AgentResult (symbolic static analysis).
    """
    code = hunk.get("code") or ""
    filename = hunk.get("file") or "<hunk>"

    findings = analyze_code_blob(code, filename=filename)
    result = AgentResult(
        agent_name="symbolic",
        findings=findings,
        raw={"checked_at": datetime.utcnow().isoformat(), "tool": "builtin-ast"},
        time_ms=None,
    )
    return result