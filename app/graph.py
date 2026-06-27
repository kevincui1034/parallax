"""LangGraph-style pipeline runner for Agent Visual Manual.

Simple async pipeline (not using langgraph directly to keep deps minimal):
receive_image → analyze_object → search_context → generate_videos → build_artifact → complete_job
"""

import logging

from .nodes.receive_image import receive_image
from .nodes.analyze_object import analyze_object
from .nodes.search_context import search_context
from .nodes.generate_videos import generate_videos
from .nodes.build_artifact import build_artifact
from .nodes.complete_job import complete_job

logger = logging.getLogger(__name__)


async def run_pipeline(initial_state: dict) -> dict:
    """Run the full visual manual pipeline.

    Each node receives and returns the shared state dict.
    If a node sets status to "blocked", the pipeline stops early.
    """
    state = initial_state

    nodes = [
        ("receive_image", receive_image),
        ("analyze_object", analyze_object),
        ("search_context", search_context),
        ("generate_videos", generate_videos),
        ("build_artifact", build_artifact),
        ("complete_job", complete_job),
    ]

    for name, node_fn in nodes:
        logger.info("pipeline: running node=%s, status=%s", name, state.get("status"))
        state = await node_fn(state)

        if state.get("status") == "blocked" and name != "complete_job":
            logger.warning("pipeline: blocked at node=%s — stopping early", name)
            state["progress"] = 100
            break

    return state
