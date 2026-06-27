"""Web search integration using Gemini native google_search grounding.

Replaces Tavily. Uses the GMI MaaS generateContent endpoint with
tools: [{"google_search": {}}] to get grounded answers with citations
directly from Gemini 3.5 Flash.
"""

import json
import logging
import re
from typing import Optional

import httpx

from .config import load_settings

logger = logging.getLogger(__name__)


async def gemini_grounded_search(query: str) -> dict:
    """Search using Gemini native google_search grounding.

    Returns {answer, citations: [{title, url, snippet}]}.
    """
    settings = load_settings()
    if not settings.gmi_maas_api_key:
        logger.info("search: GMI_MAAS_API_KEY not configured — skipping grounded search")
        return {"answer": "", "citations": []}

    # Use the generateContent endpoint (not chat/completions) for google_search tool
    model = settings.gmi_models.split(",")[0].strip()
    # The generateContent endpoint uses the raw model name without org prefix
    raw_model = model.split("/")[-1] if "/" in model else model

    url = f"{settings.gmi_maas_base_url}/models/{raw_model}:generateContent"

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": query}],
            }
        ],
        "tools": [
            {
                "google_search": {}
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {settings.gmi_maas_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning("search: Gemini grounded search returned %d — %s", resp.status_code, resp.text[:300])
                return {"answer": "", "citations": []}

            data = resp.json()

            # Extract text answer from candidates
            answer = ""
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        answer += part["text"]

            # Extract grounding citations from groundingMetadata
            citations = []
            grounding_meta = (
                candidates[0].get("groundingMetadata", {})
                if candidates
                else {}
            )
            grounding_chunks = grounding_meta.get("groundingChunks", [])
            for chunk in grounding_chunks:
                web = chunk.get("web", {})
                if web.get("uri"):
                    citations.append({
                        "title": web.get("title", ""),
                        "url": web.get("uri", ""),
                        "snippet": "",
                    })

            # Also extract grounding supports for snippets
            grounding_supports = grounding_meta.get("groundingSupports", [])
            for support in grounding_supports:
                snippet = support.get("segment", {}).get("text", "")
                idx = support.get("groundingChunkIndices", [None])
                if idx and idx[0] is not None and idx[0] < len(citations):
                    if not citations[idx[0]]["snippet"]:
                        citations[idx[0]]["snippet"] = snippet[:200]

            logger.info("search: Gemini grounded search for '%s' — %d citations", query[:50], len(citations))
            return {"answer": answer, "citations": citations}
    except Exception as e:
        logger.warning("search: Gemini grounded search error — %s", e)
        return {"answer": "", "citations": []}


async def search_object_context(object_type: str, likely_model: str = "") -> dict:
    """Search for official context about the detected object using Gemini google_search.

    Returns {queries, results, citations}.
    """
    queries = []
    if likely_model and likely_model != "unknown":
        queries.append(f"{likely_model} official documentation parts manual specifications")
        queries.append(f"{likely_model} parts diagram layout identification")
    if object_type and object_type != "unknown object":
        queries.append(f"{object_type} parts identification guide technical specifications")
    if not queries:
        logger.info("search_context: no queries — object type and model unknown")
        return {"queries": [], "results": [], "citations": []}

    all_results = []
    citations = []

    for q in queries:
        result = await gemini_grounded_search(q)
        for cite in result["citations"]:
            all_results.append({
                "query": q,
                "title": cite["title"],
                "url": cite["url"],
                "snippet": cite["snippet"],
                "score": 0.8,  # Gemini grounded results are high-confidence
            })
            citations.append({
                "claim_id": f"search_{len(citations)}",
                "source_type": "google_search_grounded",
                "url": cite["url"],
                "title": cite["title"],
                "used_for": "object identification and part context",
                "snippet": cite["snippet"][:200],
            })

    logger.info("search_context: %d queries, %d results, %d citations", len(queries), len(all_results), len(citations))
    return {
        "queries": queries,
        "results": all_results,
        "citations": citations,
    }
