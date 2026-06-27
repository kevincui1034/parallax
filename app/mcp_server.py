"""Real Model Context Protocol (MCP) server over Streamable HTTP (JSON-RPC 2.0).

External agents (Claude Desktop, Cursor, custom agents) connect to a SINGLE POST
endpoint at /mcp and speak JSON-RPC 2.0. This gives an outside agent the ability to
upload an object image, get it broken down into parts, and chat about any part — as
if it were holding the object's physical manual.

Methods implemented: initialize, notifications/initialized, ping, tools/list,
tools/call, resources/list, resources/read, prompts/list, prompts/get.

Tools:
  - create_manual_from_image_url(image_url): run the full pipeline, return job_id + parts
  - get_manual(job_id): object summary, parts, warnings, citations
  - list_parts(job_id): part list
  - get_part(job_id, part_id): one part card
  - ask_manual(job_id, question): grounded answer about the object/parts (+ citations)
  - get_manual_urls(job_id): HTML + PDF URLs
"""

import logging
import os
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .jobs import create_job, get_job, update_job
from .llm import gmi_chat, parse_json_response
from .pipeline_runner import run_and_persist
from . import storage
from .adapters import snaplii as snaplii_adapter

logger = logging.getLogger(__name__)

router = APIRouter()

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "agent-visual-manual", "version": "1.0.0"}


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    return str(request.base_url).rstrip("/")


# ─── Tool definitions (JSON Schema) ─────────────────────────────────────────

TOOLS = [
    {
        "name": "create_manual_from_image_url",
        "description": "Upload an object image by URL. Runs vision + web-search grounding and returns a structured visual manual (parts, descriptions, citations). Use this first; it returns a job_id used by the other tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "Public http(s) URL of a single clear product/object photo."}
            },
            "required": ["image_url"],
        },
    },
    {
        "name": "get_manual",
        "description": "Get the full manual summary for a job: object type, summary, all parts, warnings, and citations.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "list_parts",
        "description": "List all identified parts for a job (id, label, description, confidence).",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_part",
        "description": "Get a single part card by part id, including description, confidence, sources and unknowns.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}, "part_id": {"type": "string"}},
            "required": ["job_id", "part_id"],
        },
    },
    {
        "name": "ask_manual",
        "description": "Ask a natural-language question about the object or any of its parts. Answered strictly from the generated manual + search citations, as if reading the physical manual.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}, "question": {"type": "string"}},
            "required": ["job_id", "question"],
        },
    },
    {
        "name": "get_manual_urls",
        "description": "Get the interactive HTML manual URL and the downloadable PDF URL for a job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "create_snaplii_manual_card",
        "description": "Create a Snaplii 'Save / Share' action card for a completed manual. Returns a card with action_id and status. Requires user approval — never auto-sends.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The manual job ID."},
                "label": {"type": "string", "description": "Custom label for the card (optional)."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "create_snaplii_parts_action",
        "description": "Create a Snaplii parts/tools purchase handoff action card. Lists identified parts for the user to review on Snaplii. Never auto-purchases — requires explicit user approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The manual job ID."},
                "label": {"type": "string", "description": "Custom label for the card (optional)."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "get_snaplii_action_status",
        "description": "Get the current status of a Snaplii action card by action_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The manual job ID."},
                "action_id": {"type": "string", "description": "The Snaplii action ID to check."},
            },
            "required": ["job_id", "action_id"],
        },
    },
    {
        "name": "attach_snaplii_action_to_manual",
        "description": "Attach a Snaplii action to a manual job's snaplii_actions list. Used after creating an action to persist it on the job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The manual job ID."},
                "action": {"type": "object", "description": "The action card dict returned by create_snaplii_*."},
            },
            "required": ["job_id", "action"],
        },
    },
]

PROMPTS = [
    {
        "name": "explain_object",
        "description": "Explain an object and its parts from a manual job.",
        "arguments": [{"name": "job_id", "description": "Manual job id", "required": True}],
    },
]


# ─── Tool implementations ───────────────────────────────────────────────────

async def _tool_create_manual(args: dict, request: Request) -> dict:
    image_url = (args or {}).get("image_url", "").strip()
    if not image_url:
        return {"error": "image_url is required"}
    data = await storage.get_bytes_from_url(image_url)
    if not data:
        return {"error": f"could not fetch image_url: {image_url}"}

    job = create_job()
    fname = os.path.basename(image_url.split("?")[0]) or "image.png"
    # Persist the source image (Blob in prod, disk locally) and use that URL downstream.
    if storage.is_blob():
        ext = os.path.splitext(fname)[1].lstrip(".").lower() or "png"
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
        stored = await storage.put_bytes(f"uploads/{job.job_id}/{fname}", data, ctype)
        input_images = [{"url": stored, "public_url": stored, "role": "main object photo"}]
    else:
        input_images = [{"url": image_url, "public_url": image_url, "role": "main object photo"}]
    update_job(job.job_id, input_images=input_images)

    await run_and_persist(job.job_id, input_images, None)
    final = get_job(job.job_id)
    if not final:
        return {"error": "job not found after run"}
    base = _public_base(request)
    return {
        "job_id": final.job_id,
        "status": final.status,
        "object_type": final.object_type,
        "likely_model": final.likely_model,
        "object_summary": final.object_summary,
        "parts": [
            {"part_id": p.get("id"), "label": p.get("label"), "description": p.get("description", ""), "confidence": p.get("confidence", 0.0)}
            for p in (final.parts or [])
        ],
        "citations_count": len(final.citations or []),
        "html_url": f"{base}/api/jobs/{final.job_id}/manual",
        "pdf_url": f"{base}/api/jobs/{final.job_id}/manual.pdf",
    }


def _tool_get_manual(args: dict) -> dict:
    job = get_job((args or {}).get("job_id", ""))
    if not job:
        return {"error": "job not found"}
    return {
        "job_id": job.job_id,
        "status": job.status,
        "object": {"type": job.object_type, "likely_model": job.likely_model, "summary": job.object_summary, "confidence": job.object_confidence},
        "parts": job.parts or [],
        "warnings": job.warnings or [],
        "citations": job.citations or [],
    }


def _tool_list_parts(args: dict) -> dict:
    job = get_job((args or {}).get("job_id", ""))
    if not job:
        return {"error": "job not found"}
    return {"parts": [
        {"part_id": p.get("id"), "label": p.get("label"), "description": p.get("description", ""), "confidence": p.get("confidence", 0.0), "source_status": p.get("source_status", "vision_inferred")}
        for p in (job.parts or [])
    ]}


def _tool_get_part(args: dict) -> dict:
    job = get_job((args or {}).get("job_id", ""))
    if not job:
        return {"error": "job not found"}
    part_id = (args or {}).get("part_id", "")
    part = next((p for p in (job.parts or []) if p.get("id") == part_id), None)
    if not part:
        return {"error": f"part '{part_id}' not found"}
    return part


async def _tool_ask_manual(args: dict) -> dict:
    job = get_job((args or {}).get("job_id", ""))
    if not job:
        return {"error": "job not found"}
    question = (args or {}).get("question", "")
    parts_desc = "\n".join(
        f"- {p.get('label')} ({p.get('id')}): {p.get('description', '')}" for p in (job.parts or [])
    )
    system = (
        "You are a manual assistant. Answer ONLY from the provided object data and citations. "
        "If the manual does not contain the answer, say so. Be concise.\n\n"
        f"Object: {job.object_type} ({job.likely_model}). Summary: {job.object_summary}\n"
        f"Parts:\n{parts_desc}\n"
    )
    reply = await gmi_chat(question, system=system)
    return {"answer": reply or "No answer available.", "citations": job.citations or []}


def _tool_get_urls(args: dict, request: Request) -> dict:
    job = get_job((args or {}).get("job_id", ""))
    if not job:
        return {"error": "job not found"}
    base = _public_base(request)
    return {
        "html_url": f"{base}/api/jobs/{job.job_id}/manual",
        "pdf_url": f"{base}/api/jobs/{job.job_id}/manual.pdf",
    }


async def _tool_create_snaplii_manual_card(args: dict, request: Request) -> dict:
    job_id = (args or {}).get("job_id", "")
    job = get_job(job_id)
    if not job:
        return {"error": "job not found"}
    if job.status not in ("completed", "partial"):
        return {"error": "manual not ready — Snaplii actions available after completion"}
    label = (args or {}).get("label", "") or "Save / Share with Snaplii"
    base = _public_base(request)
    manual_url = f"{base}/api/jobs/{job_id}/manual"
    action = await snaplii_adapter.create_manual_card(job_id, manual_url=manual_url, label=label)
    # Persist on job
    current = job.snaplii_actions or []
    current.append(action)
    update_job(job_id, snaplii_actions=current)
    return action


async def _tool_create_snaplii_parts_action(args: dict, request: Request) -> dict:
    job_id = (args or {}).get("job_id", "")
    job = get_job(job_id)
    if not job:
        return {"error": "job not found"}
    if job.status not in ("completed", "partial"):
        return {"error": "manual not ready — Snaplii actions available after completion"}
    label = (args or {}).get("label", "") or "View Parts & Tools on Snaplii"
    action = await snaplii_adapter.create_parts_action(job_id, parts=job.parts or [], label=label)
    current = job.snaplii_actions or []
    current.append(action)
    update_job(job_id, snaplii_actions=current)
    return action


async def _tool_get_snaplii_action_status(args: dict) -> dict:
    job_id = (args or {}).get("job_id", "")
    action_id = (args or {}).get("action_id", "")
    job = get_job(job_id)
    if not job:
        return {"error": "job not found"}
    for a in (job.snaplii_actions or []):
        if a.get("id") == action_id:
            return a
    status = await snaplii_adapter.get_action_status(action_id)
    if status:
        return status
    return {"error": "action not found"}


def _tool_attach_snaplii_action(args: dict) -> dict:
    job_id = (args or {}).get("job_id", "")
    action = (args or {}).get("action", {})
    job = get_job(job_id)
    if not job:
        return {"error": "job not found"}
    if not action or not isinstance(action, dict):
        return {"error": "action dict is required"}
    current = job.snaplii_actions or []
    # Avoid duplicates by action id
    existing_ids = {a.get("id") for a in current}
    if action.get("id") not in existing_ids:
        current.append(action)
        update_job(job_id, snaplii_actions=current)
    return {"job_id": job_id, "action_id": action.get("id", ""), "attached": True, "total_actions": len(current)}


async def _dispatch_tool(name: str, args: dict, request: Request) -> dict:
    if name == "create_manual_from_image_url":
        return await _tool_create_manual(args, request)
    if name == "get_manual":
        return _tool_get_manual(args)
    if name == "list_parts":
        return _tool_list_parts(args)
    if name == "get_part":
        return _tool_get_part(args)
    if name == "ask_manual":
        return await _tool_ask_manual(args)
    if name == "get_manual_urls":
        return _tool_get_urls(args, request)
    if name == "create_snaplii_manual_card":
        return await _tool_create_snaplii_manual_card(args, request)
    if name == "create_snaplii_parts_action":
        return await _tool_create_snaplii_parts_action(args, request)
    if name == "get_snaplii_action_status":
        return await _tool_get_snaplii_action_status(args)
    if name == "attach_snaplii_action_to_manual":
        return _tool_attach_snaplii_action(args)
    return {"error": f"unknown tool: {name}"}


# ─── JSON-RPC plumbing ──────────────────────────────────────────────────────

def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _handle_rpc(message: dict, request: Request):
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no id) — acknowledge with 202, no body.
    if req_id is None:
        return None

    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        try:
            data = await _dispatch_tool(name, args, request)
        except Exception as e:  # noqa: BLE001
            logger.error("mcp tool %s failed: %s", name, e)
            return _result(req_id, {"content": [{"type": "text", "text": f"Tool error: {e}"}], "isError": True})
        import json as _json
        is_error = isinstance(data, dict) and "error" in data
        return _result(req_id, {"content": [{"type": "text", "text": _json.dumps(data)}], "isError": is_error})
    if method == "resources/list":
        return _result(req_id, {"resources": []})
    if method == "resources/read":
        return _error(req_id, -32601, "resources/read not supported; use tools")
    if method == "prompts/list":
        return _result(req_id, {"prompts": PROMPTS})
    if method == "prompts/get":
        name = params.get("name", "")
        job_id = (params.get("arguments") or {}).get("job_id", "")
        return _result(req_id, {
            "messages": [
                {"role": "user", "content": {"type": "text", "text": f"Use get_manual with job_id={job_id} and explain the object and its parts."}}
            ]
        })
    return _error(req_id, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """Streamable HTTP MCP endpoint (JSON-RPC 2.0)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)

    # Batch support
    if isinstance(body, list):
        responses = []
        for msg in body:
            r = await _handle_rpc(msg, request)
            if r is not None:
                responses.append(r)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    resp = await _handle_rpc(body, request)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp)


@router.get("/mcp")
async def mcp_get():
    """Some clients probe with GET; report the endpoint is alive."""
    return JSONResponse({"transport": "streamable-http", "protocolVersion": PROTOCOL_VERSION, "serverInfo": SERVER_INFO})
