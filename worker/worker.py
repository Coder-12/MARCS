# worker/worker.py
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

import asyncio
import signal
from contextlib import suppress

from core.logging import get_logger
from orchestration.queue import dequeue_event, queue_size

from core.tracing import start_span, get_trace_id, get_span_id
from agents.router_agent import RouterAgent
from agents.code_quality_agent import CodeQualityAgent
from agents.security_agent import SecurityAgent
from agents.pipeline import run_pipeline
# after pipeline execution
from agents.patch_generator import PatchGenerator
from services.review_store import save_review
from agents.symbolic_agent import SymbolicAgent

from services.normalizer import normalize_review
from services.deduper import dedupe_findings
from services.patch_selector import annotate_and_select


# ---------- Phase-0 v1 linking (still present for backward compatibility)
from services.patch_linker import link_patches

# ---------- Phase-0 v2 scoring + selection + linking
from services.patch_scorer import score_all_patches
from services.patch_selector_pro import select_patches_for_review
from services.patch_linker_v2 import link_patches_v2

# Create router (ideally inject once at startup)
router = RouterAgent([SymbolicAgent(), CodeQualityAgent(), SecurityAgent()])

logger = get_logger("worker.main")
patcher = PatchGenerator()  # ideally create once at module top

# Global flag for stopping loop gracefully
shutdown_flag = False


async def process_event(evt):
    """
    Phase 0 worker handler.
    Step 0.27: Fully deterministic, Pro-first selection pipeline.

    In Phase 1+ this function becomes the orchestrator:
    - fan-out to agents
    - LLM patch generators
    - retries, backoff, priorities
    - full SOTA orchestration
    """
    with start_span("worker.process", parent_span_id=evt.span_id):
        logger.info(
            "worker_process_start",
            event_type=evt.event_type,
            delivery_id=evt.delivery_id,
            repo=evt.repo_full_name,
        )

        # Lightweight simulation
        await asyncio.sleep(0.1)

        # ----------------------------------------------------------------------
        # 1. Run extraction + static analysis + turn raw outputs into a Review
        # ----------------------------------------------------------------------
        review = await run_pipeline(evt, router)

        # ----------------------------------------------------------------------
        # 2. Generate patches
        #    Try real LLM patches (Step-4) when enabled; otherwise fall back to Phase-0 patcher.
        # ----------------------------------------------------------------------
        context = {"trace_id": evt.trace_id, "span_id": evt.span_id, "payload": getattr(evt, "payload", {})}

        # Attempt real LLM patches first (defensive; returns {} on any failure)
        try:
            from services.patch_integration import try_real_llm_patches
            real_patches = await try_real_llm_patches(review, context=context)
        except Exception:
            real_patches = {}

        if real_patches:
            generated = real_patches
        else:
            # Phase-0 deterministic generator fallback
            generated = await patcher.generate_patches_for_review(review, context)

        # Attach raw patches (top-level only!)
        review.suggested_patches = generated

        # ----------------------------------------------------------------------
        # 3. Normalize findings (fingerprint, severity fill, evidence_score)
        # ----------------------------------------------------------------------
        review = normalize_review(review)

        # ----------------------------------------------------------------------
        # 4. Deduplicate repeated findings deterministically
        # ----------------------------------------------------------------------
        review, _mapping = dedupe_findings(review)

        # ----------------------------------------------------------------------
        # 5. RANK & SELECT patches — Pro Selector first, fallback to Phase-0
        # ----------------------------------------------------------------------
        selected_map = {}

        try:
            # Try Pro selector (v2)
            from services.patch_selector_pro import select_patches_for_review as pro_select
            sel_meta = pro_select(review)   # updates review.metadata["selected_patches"]

        except Exception:
            # Phase-0 fallback
            from services.patch_selector import annotate_and_select
            annotate_and_select(review)
            sel_meta = review.metadata.get("selected_patches", {})

        # Flatten selected patches {patch_id → text}
        for fid, items in (sel_meta or {}).items():
            for info in items:
                pid = info.get("patch_id")
                if pid in generated:
                    selected_map[pid] = generated[pid]

        # Replace suggested_patches with only SELECTED ones
        review.suggested_patches = selected_map

        # ----------------------------------------------------------------------
        # 6. Link patches → v2 first, fallback to v1 for test compatibility
        # ----------------------------------------------------------------------
        try:
            from services.patch_linker_v2 import link_patches_v2
            review = link_patches_v2(review)
        except Exception:
            pass  # optional

        # Always run legacy linker to keep golden tests stable
        from services.patch_linker import link_patches
        review = link_patches(review)

        # ----------------------------------------------------------------------
        # PHASE 1 — REAL LLM OVERRIDE (optional)
        # If MACRS_LLM_MODE=real → bypass Phase-0 pipeline review and replace with LLM-generated review
        # ----------------------------------------------------------------------
        from services.reviewer_integration import generate_review_for_event

        real_review_dict = await generate_review_for_event(evt)
        if real_review_dict is not None:
            logger.info(
                "real_llm_review_used",
                event_id=evt.delivery_id,
                backend=os.environ.get("MACRS_LLM_MODE", "test")
            )
            try:
                from agents.results import SynthesizedReview
                review = SynthesizedReview(**real_review_dict)
            except Exception:
                review = real_review_dict

        # ----------------------------------------------------------------------
        # 7. STEP 0.30 — Canonical Event-ID alignment
        # ----------------------------------------------------------------------
        # Ensure the stored review has the SAME ID as the incoming event
        review.event_id = evt.delivery_id

        # ----------------------------------------------------------------------
        # 8. Persist final Review
        # ----------------------------------------------------------------------
        await save_review(review)

        logger.info(
            "pipeline_reviewed",
            event_id=evt.delivery_id,
            findings=len(review.findings),
            patches=len(selected_map),
        )

        logger.info(
            "worker_process_complete",
            delivery_id=evt.delivery_id,
            event_type=evt.event_type,
            priority=evt.priority,
        )


async def worker_loop(poll_interval: float = 0.5):
    """
    The main worker loop.
    - Infinite loop until shutdown_flag
    - Dequeues events
    - Processes them
    """
    global shutdown_flag

    logger.info("worker_started")

    while not shutdown_flag:
        evt = dequeue_event()
        if evt is None:
            # nothing in queue → sleep briefly
            await asyncio.sleep(poll_interval)
            continue

        try:
            await process_event(evt)
        except Exception as e:
            logger.error(
                "worker_event_error",
                delivery_id=evt.delivery_id,
                error=str(e),
            )

    logger.info("worker_exiting")


def request_shutdown():
    """Signal handler hook."""
    global shutdown_flag
    logger.info("worker_shutdown_requested")
    shutdown_flag = True


async def main():
    """Entry point for standalone worker execution."""
    # Install signal handlers (CTRL+C)
    # await asyncio.sleep(5)  # slow worker
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(s, request_shutdown)

    await worker_loop()


if __name__ == "__main__":
    asyncio.run(main())