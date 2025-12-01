# eval/schema.py
from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional

class ExpectedResult(BaseModel):
    min_findings: int = 0
    max_findings: Optional[int] = None
    categories: Optional[List[str]] = None
    min_patches: int = 0
    allow_extra_findings: bool = True

class GoldenCase(BaseModel):
    id: str
    description: str
    headers: Dict[str, str] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    expected: ExpectedResult