# api/routes/debug_eval.py
from fastapi import APIRouter, HTTPException
from core.logging import get_logger
from services.symbolic_eval import analyze_hunk
from orchestration.queue import dequeue_event, queue_size
from orchestration.types import InternalEvent

router = APIRouter(prefix="/debug")
logger = get_logger("api.debug_eval")

@router.post("/eval_hunk")
async def eval_hunk(payload: dict):
    """
    Accepts a JSON body with minimal hunk structure; returns symbolic findings.
    Useful for quick manual testing.
    Example payload:
    { "code": "import os\nos.system('ls')", "file":"a.py", "delivery_id":"1", "repo_full_name":"me/repo" }
    """
    try:
        findings = analyze_hunk(payload)
        return {"status": "ok", "agent_result": findings.model_dump()}
    except Exception as e:
        logger.error("eval_hunk_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))