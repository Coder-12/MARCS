# tools/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseTool(ABC):
    """
    Abstract base class for static analysis tools.
    All tools must implement `run()` and return a structured dict.
    """

    name: str = "base_tool"

    @abstractmethod
    async def run(self, file_path: str, code: str, **kwargs) -> Dict[str, Any]:
        """
        Execute the tool on the given code or file.
        Returns structured output:
        {
            "tool": str,
            "success": bool,
            "findings": list,
            "raw_output": str | None
        }
        """
        raise NotImplementedError
