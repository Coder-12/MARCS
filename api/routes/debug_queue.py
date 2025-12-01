from fastapi import APIRouter
from orchestration.queue import queue_size, dequeue_event

router = APIRouter(prefix="/debug")

@router.get("/queue_size")
async def get_size():
    return {"size": queue_size()}

@router.post("/queue_pop")
async def pop():
    evt = dequeue_event()
    if evt is None:
        return {"event": None}
    return {"event": evt.model_dump()}