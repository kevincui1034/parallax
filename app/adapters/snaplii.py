"""Snaplii adapter — placeholder-safe methods for post-manual action layer.

Snaplii acts as a completion layer AFTER the visual manual is generated.
It never interrupts the core pipeline. All methods return mock data when
the Snaplii API is unavailable (no API key configured).

Action types:
  - manual_card: Save / Share the manual via Snaplii
  - parts_action: Parts & tools purchase handoff
  - reward_claim: Reward / claim card

All actions require user approval — no auto-purchase, no auto-send.
"""

import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_SNAPLII_API_BASE = os.environ.get("SNAPLII_API_BASE", "https://api.snaplii.com/v1")
_SNAPLII_API_KEY = os.environ.get("SNAPLII_API_KEY", "")
_SNAPLII_WEBHOOK_SECRET = os.environ.get("SNAPLII_WEBHOOK_SECRET", "")


def is_configured() -> bool:
    """Return True if Snaplii API key is set."""
    return bool(_SNAPLII_API_KEY)


def _mock_action(action_type: str, label: str, job_id: str, metadata: dict | None = None) -> dict:
    """Create a placeholder action card when Snaplii API is not available."""
    action_id = f"snap_{uuid.uuid4().hex[:12]}"
    return {
        "id": action_id,
        "type": action_type,
        "status": "pending",
        "label": label,
        "url": "",
        "job_id": job_id,
        "created_at": time.time(),
        "requires_user_approval": True,
        "mock": True,
        "metadata": metadata or {},
    }


async def create_manual_card(job_id: str, manual_url: str = "", label: str = "Save / Share with Snaplii") -> dict:
    """Create a Snaplii action card for saving/sharing a completed manual.

    Returns an action card dict. If Snaplii API is not configured, returns a
    mock card with status 'pending' so the frontend can still render the CTA.
    """
    if not is_configured():
        logger.info("snaplii: create_manual_card returning mock (API not configured) for job %s", job_id)
        return _mock_action("manual_card", label, job_id, {"manual_url": manual_url})

    # Real API call (placeholder for when official docs are available)
    # POST {_SNAPLII_API_BASE}/manual-cards
    # body: {job_id, manual_url, label}
    # headers: Authorization: Bearer {_SNAPLII_API_KEY}
    logger.info("snaplii: create_manual_card for job %s (API configured but endpoint TBD)", job_id)
    return _mock_action("manual_card", label, job_id, {"manual_url": manual_url})


async def create_parts_action(
    job_id: str,
    parts: list[dict] | None = None,
    label: str = "View Parts & Tools on Snaplii",
) -> dict:
    """Create a Snaplii parts/tools purchase handoff action.

    This never auto-purchases. It creates a card that the user can click to
    view recommended parts/tools on Snaplii, requiring explicit approval.
    """
    parts = parts or []
    if not is_configured():
        logger.info("snaplii: create_parts_action returning mock (API not configured) for job %s", job_id)
        return _mock_action("parts_action", label, job_id, {"parts_count": len(parts), "part_ids": [p.get("id", "") for p in parts]})

    # Real API call (placeholder)
    # POST {_SNAPLII_API_BASE}/parts-actions
    # body: {job_id, parts: [...], label}
    logger.info("snaplii: create_parts_action for job %s with %d parts (API configured but endpoint TBD)", job_id, len(parts))
    return _mock_action("parts_action", label, job_id, {"parts_count": len(parts), "part_ids": [p.get("id", "") for p in parts]})


async def create_reward_claim(job_id: str, label: str = "Claim Your Reward") -> dict:
    """Create a Snaplii reward/claim card action."""
    if not is_configured():
        logger.info("snaplii: create_reward_claim returning mock (API not configured) for job %s", job_id)
        return _mock_action("reward_claim", label, job_id, {})

    # Real API call (placeholder)
    logger.info("snaplii: create_reward_claim for job %s (API configured but endpoint TBD)", job_id)
    return _mock_action("reward_claim", label, job_id, {})


async def get_action_status(action_id: str) -> dict | None:
    """Get the status of a Snaplii action by ID.

    Returns None if the action cannot be found.
    """
    if not is_configured():
        logger.info("snaplii: get_action_status returning mock for action %s (API not configured)", action_id)
        return {
            "id": action_id,
            "status": "pending",
            "mock": True,
        }

    # Real API call (placeholder)
    # GET {_SNAPLII_API_BASE}/actions/{action_id}
    logger.info("snaplii: get_action_status for %s (API configured but endpoint TBD)", action_id)
    return {
        "id": action_id,
        "status": "pending",
        "mock": True,
    }


async def handle_webhook(payload: dict) -> dict:
    """Handle a webhook callback from Snaplii.

    Updates action status based on webhook data. In placeholder mode, just
    acknowledges receipt.
    """
    action_id = payload.get("action_id", "")
    new_status = payload.get("status", "")
    logger.info("snaplii: webhook received for action %s, status=%s", action_id, new_status)

    if not is_configured():
        return {"received": True, "mock": True, "action_id": action_id, "status": new_status}

    # Real webhook processing would verify signature and update persisted state
    return {"received": True, "action_id": action_id, "status": new_status}


def get_default_actions(job_id: str, manual_url: str = "", parts: list[dict] | None = None) -> list[dict]:
    """Return the default set of Snaplii action cards for a completed manual.

    By default, shows a single CTA: 'Save / Share with Snaplii'.
    The parts_action card is included but marked as secondary.
    """
    actions = [
        _mock_action("manual_card", "Save / Share with Snaplii", job_id, {"manual_url": manual_url}),
    ]
    if parts:
        actions.append(
            _mock_action("parts_action", "View Parts & Tools on Snaplii", job_id, {"parts_count": len(parts)})
        )
    return actions
