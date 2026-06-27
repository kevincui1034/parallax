"""
Agent brain — 3.5 Flash on GMI. Takes the user's message + part inventory,
returns a reply plus a list of viewer actions, validated against the frozen
action schema in contract.py (unknown action types are dropped).
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

import gmi
from contract import AgentAction, AgentRequest, AgentResponse

_ACTION_ADAPTER = TypeAdapter(AgentAction)

SYSTEM_PROMPT = """\
You are Parallax's agent. You answer questions about a 3D exploded-parts diagram \
and ACT on the viewer by emitting actions the frontend executes in order.

Respond ONLY with a JSON object of this exact shape:
{
  "reply": "<short natural-language answer for the user>",
  "actions": [ <zero or more action objects> ]
}

Allowed actions (use ONLY these types and fields):
  {"type": "explode",   "factor": <number 0..1>}      // set the explode amount
  {"type": "highlight", "part_id": "<id>"}             // emphasize one part
  {"type": "isolate",   "part_ids": ["<id>", ...]}     // show only these parts
  {"type": "focus",     "part_id": "<id>"}             // frame one part
  {"type": "reset"}                                     // reassemble, clear view

Rules:
- Only reference part_id values that appear in the provided inventory.
- Prefer 1-3 actions that directly serve the request. Use "reset" before a fresh layout.
- If the model has a single part, do not emit explode/isolate.
- Keep "reply" concise and specific.
"""


def _inventory(parts: list[dict[str, Any]] | None) -> str:
    if not parts:
        return "(no part inventory provided)"
    lines = []
    for p in parts:
        pid = p.get("part_id") or p.get("id") or "?"
        label = p.get("label") or p.get("name") or pid
        note = p.get("note")
        lines.append(f"- {pid}: {label}" + (f" — {note}" if note else ""))
    return "\n".join(lines)


def _validate_actions(raw: Any) -> list[AgentAction]:
    """Validate each action against the schema; drop anything invalid."""
    out: list[AgentAction] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        try:
            out.append(_ACTION_ADAPTER.validate_python(item))
        except ValidationError:
            continue  # unknown/invalid action types are dropped, not passed through
    return out


async def run_agent(
    req: AgentRequest, parts: list[dict[str, Any]] | None = None
) -> AgentResponse:
    """Call 3.5 Flash and return a validated {reply, actions}."""
    user = (
        f"Current explode factor: {req.explode_factor}.\n"
        f"Part inventory:\n{_inventory(parts)}\n\n"
        f"User: {req.message}"
    )
    content = await gmi.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        json_mode=True,
    )

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Model didn't return clean JSON — surface its text, no actions.
        return AgentResponse(reply=content.strip()[:500], actions=[])

    reply = str(data.get("reply", "")).strip()
    actions = _validate_actions(data.get("actions"))
    return AgentResponse(reply=reply, actions=actions)
