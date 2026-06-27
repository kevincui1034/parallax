"""Search context node — use web search to enrich part analysis with sources."""

import logging

from ..search import search_object_context

logger = logging.getLogger(__name__)


async def search_context(state: dict) -> dict:
    """Search for official context about the detected object."""
    object_type = state.get("object_type", "")
    likely_model = state.get("likely_model", "")

    state["status"] = "searching"
    state["progress"] = 35
    state["simple_message"] = "Searching for official context..."

    result = await search_object_context(object_type, likely_model)

    state["search_queries"] = result.get("queries", [])
    state["search_results"] = result.get("results", [])

    # Merge citations
    existing_citations = state.get("citations", [])
    new_citations = result.get("citations", [])
    existing_citations.extend(new_citations)
    state["citations"] = existing_citations

    # Update parts with source support if we found relevant results
    if result.get("results"):
        parts = state.get("parts", [])
        for p in parts:
            # Check if any search result mentions this part label
            for r in result["results"]:
                if p.get("label", "").lower() in r.get("title", "").lower() or p.get("label", "").lower() in r.get("snippet", "").lower():
                    p["source_status"] = "search_supported"
                    p["sources"] = p.get("sources", []) + [{"url": r["url"], "title": r["title"]}]
                    break
        state["parts"] = parts

    queries_count = len(state.get("search_queries", []))
    results_count = len(state.get("search_results", []))
    citations_count = len(state.get("citations", []))

    state["status"] = "planning"
    state["progress"] = 40
    state["simple_message"] = f"Search complete: {queries_count} queries, {results_count} results, {citations_count} citations."

    logger.info("search_context: %d queries, %d results, %d citations", queries_count, results_count, citations_count)
    return state
