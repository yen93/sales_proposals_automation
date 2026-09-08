"""OCR + entity extraction from the handwritten demo-notes photo via OpenAI vision."""

import base64

import config
from src import llm_client

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
    encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{encoded}"

    if mime_type == "application/pdf":
        source_block = {"type": "input_file", "filename": "demo_notes.pdf", "file_data": data_url}
    else:
        source_block = {"type": "input_image", "image_url": data_url}

    content = [
        source_block,
        {
            "type": "input_text",
            "text": (
                "Transcribe this handwritten sales demo-call notes page and extract "
                "the fields defined in extract_demo_notes. If handwriting is illegible "
                "or a field isn't on the page, leave it as an empty string and list its "
                "name in unclear_fields rather than guessing."
            ),
        },
    ]

    return llm_client.call_tool(content, EXTRACTION_TOOL)


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
