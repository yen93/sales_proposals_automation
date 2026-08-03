"""Looks up the Fathom call recording matching a demo-notes email, and
surfaces its content (pricing, special requests, instructions) so it can
enrich proposal generation. Best-effort: returns None if no API key is
configured, the API call fails, or no confident match is found — a missing
recording should never block the pipeline."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
import requests

import config

log = logging.getLogger("fathom_service")

MODEL = "claude-opus-5"
API_BASE = "https://api.fathom.ai/external/v1"

MATCH_TOOL = {
    "name": "match_meeting",
    "description": "Identify which recorded call (if any) corresponds to this client's demo notes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meeting_id": {
                "type": "string",
                "description": "The id of the matching meeting, or an empty string if none confidently match",
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
            "reasoning": {"type": "string"},
        },
        "required": ["meeting_id", "confidence", "reasoning"],
    },
}


def _meeting_id(meeting: dict) -> str:
    return str(meeting.get("id") or meeting.get("recording_id") or meeting.get("share_url") or "")


def list_recent_meetings(created_after: str) -> list[dict]:
    """Fetches meetings (with transcript/highlights) recorded since `created_after`
    (ISO 8601 UTC), following pagination."""
    meetings = []
    cursor = None
    headers = {"X-Api-Key": config.FATHOM_API_KEY}
    while True:
        params = {
            "created_after": created_after,
            "include_transcript": "true",
            "include_highlights": "true",
        }
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{API_BASE}/meetings", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        meetings.extend(data.get("items") or data.get("meetings") or [])
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return meetings


def find_matching_notes(ocr_fields: dict) -> Optional[str]:
    """Returns the matched meeting's full content as a JSON string for the
    slide-rewrite/template-selection prompts to draw pricing, special
    requests, and instructions from, or None if nothing confidently matches."""
    if not config.FATHOM_API_KEY:
        return None

    created_after = (
        datetime.now(timezone.utc) - timedelta(days=config.FATHOM_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        meetings = list_recent_meetings(created_after)
    except Exception:
        log.exception("Failed to fetch Fathom meetings; proceeding without enrichment")
        return None

    if not meetings:
        return None

    candidates = [
        {
            "id": _meeting_id(m),
            "title": m.get("title"),
            "recorded_at": m.get("recording_start_time") or m.get("scheduled_start_time"),
            "invitees": m.get("calendar_invitees") or m.get("invitees"),
        }
        for m in meetings
    ]

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[MATCH_TOOL],
        tool_choice={"type": "tool", "name": "match_meeting"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Which of these recorded calls (if any) corresponds to the demo-notes "
                    "email below? Match on client/company name, contact name, and date "
                    "proximity. Only pick one if you're reasonably confident it's the same "
                    "call — otherwise return an empty meeting_id with confidence 'none'.\n\n"
                    f"Demo notes:\n{json.dumps(ocr_fields, indent=2)}\n\n"
                    f"Candidate calls:\n{json.dumps(candidates, indent=2, default=str)}"
                ),
            }
        ],
    )

    match = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "match_meeting":
            match = block.input

    if not match or not match.get("meeting_id") or match.get("confidence") in ("none", "low"):
        return None

    meeting = next((m for m in meetings if _meeting_id(m) == match["meeting_id"]), None)
    if not meeting:
        return None

    log.info("Matched Fathom meeting %s (confidence=%s)", match["meeting_id"], match["confidence"])
    return json.dumps(meeting, indent=2, default=str)
