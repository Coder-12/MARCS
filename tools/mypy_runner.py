# tools/mypy_runner.py
from typing import Any, Dict
from tools.base import BaseTool


class MypyRunner(BaseTool):
    name = "mypy"

    async def run(self, file_path: str, code: str, **kwargs) -> Dict[str, Any]:
        return {
            "tool": self.name,
            "success": True,
            "findings": [],
            "raw_output": None,
        }
