"""LangSmith tracing setup."""

import logging
import os

logger = logging.getLogger(__name__)

_tracing_initialized = False


def setup_tracing():
    global _tracing_initialized
    if _tracing_initialized:
        return

    tracing = os.environ.get("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
    api_key = os.environ.get("LANGSMITH_API_KEY", "")

    if tracing and api_key:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = api_key
        project = os.environ.get("LANGSMITH_PROJECT", "explodeview-agent")
        os.environ["LANGCHAIN_PROJECT"] = project
        logger.info("LangSmith tracing enabled (project=%s)", project)
    else:
        logger.info("LangSmith tracing disabled (LANGSMITH_TRACING not set or key missing)")

    _tracing_initialized = True
