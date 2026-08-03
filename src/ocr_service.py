"""OCR + entity extraction from the handwritten demo-notes photo via Claude vision."""

import base64
from typing import Optional

import anthropic

import config

MODEL = "claude-opus-5"

EXTRACTION_TOOL = {
    "name": "extract_demo_notes",
    "description": "Structured fields transcribed from a handwritten demo-call notes photo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "client_org": {"type": "string", "description": "Client company/organisation name"},
            "contact_name": {"type": "string", "description": "Primary contact person at the client"},
            "event_date": {"type": "string", "description": "Event/session date if mentioned, else empty string"},
            "recommended_service": {
                "type": "string",
                "description": (
                    "The service type recommended on the call, e.g. keynote, workshop, "
                    "leadership offsite, virtual session — used to match a proposal template"
                ),
            },
            "audience_size": {"type": "string", "description": "Audience/attendee size if mentioned"},
            "location": {"type": "string", "description": "Delivery location or virtual/in-person"},
            "summary": {"type": "string", "description": "1-3 sentence summary of the client's situation and goals"},
            "scope": {"type": "string", "description": "Notes on scope, program components, or learning objectives discussed"},
            "raw_transcript": {"type": "string", "description": "Best-effort full transcription of all handwritten text on the page"},
            "unclear_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of fields above that were illegible, ambiguous, or not present on the page",
            },
        },
        "required": [
            "client_org",
            "recommended_service",
            "summary",
            "raw_transcript",
            "unclear_fields",
        ],
    },
}


def extract_fields(image_bytes: bytes, mime_type: str) -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
    block_type = "document" if mime_type == "application/pdf" else "image"

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "extract_demo_notes"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": block_type,
                        "source": {"type": "base64", "media_type": mime_type, "data": encoded},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Transcribe this handwritten sales demo-call notes page and extract "
                            "the fields defined in extract_demo_notes. If handwriting is illegible "
                            "or a field isn't on the page, leave it as an empty string and list its "
                            "name in unclear_fields rather than guessing."
                        ),
                    },
                ],
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_demo_notes":
            return block.input
    raise RuntimeError("Claude did not return the expected extract_demo_notes tool call")


def missing_required_fields(fields: dict) -> list[str]:
    missing = [name for name in config.REQUIRED_OCR_FIELDS if not fields.get(name)]
    missing += [name for name in fields.get("unclear_fields", []) if name in config.REQUIRED_OCR_FIELDS]
    # de-dupe while preserving order
    seen = set()
    result = []
    for name in missing:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result
