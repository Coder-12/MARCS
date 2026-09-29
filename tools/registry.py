# tools/registry.py
from typing import Dict, Type

from tools.base import BaseTool
from tools.pylint_runner import PylintRunner
from tools.mypy_runner import MypyRunner
from tools.bandit_runner import BanditRunner


class ToolRegistry:
    """
    Maintains a mapping from tool_name -> ToolClass.
    Allows orchestrator and agents to call tools easily.
    """

    def __init__(self):
        self._registry: Dict[str, Type[BaseTool]] = {}

    def register_tool(self, name: str, tool_cls: Type[BaseTool]) -> None:
        self._registry[name] = tool_cls

    def get(self, name: str) -> BaseTool:
        if name not in self._registry:
            raise ValueError(f"Tool '{name}' is not registered.")
        return self._registry[name]()

    def list_tools(self):
        return list(self._registry.keys())


# Global registry instance
tool_registry = ToolRegistry()

# Register Phase-0 placeholder tools
tool_registry.register_tool("pylint", PylintRunner)
tool_registry.register_tool("mypy", MypyRunner)
tool_registry.register_tool("bandit", BanditRunner)
