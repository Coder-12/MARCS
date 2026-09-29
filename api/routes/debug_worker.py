from fastapi import APIRouter
from orchestration.queue import queue_size

router = APIRouter(prefix="/worker")

@router.get("/queue")
async def get_queue_count():
    return {"queue_size": queue_size()}