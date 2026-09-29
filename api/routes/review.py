# api/routes/review.py
from fastapi import APIRouter, HTTPException
from core.logging import get_logger
from services.review_store import get_review, all_reviews
from agents.results import SynthesizedReview
from typing import Dict

router = APIRouter(prefix="/review")
logger = get_logger("api.review")

@router.get("/all", response_model=Dict)
async def fetch_all_reviews():
    m = await all_reviews()
    # convert pydantic models to dicts
    return {k: v.model_dump() for k, v in m.items()}


@router.get("/{event_id}", response_model=Dict)
async def fetch_review(event_id: str):
    """
    Fetch the synthesized review + patches for a given delivery_id (event id).
    Phase-0: in-memory store.
    """
    review = await get_review(event_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review_not_found")
    # return as simple dict (pydantic model -> dict)
    return review.model_dump()
