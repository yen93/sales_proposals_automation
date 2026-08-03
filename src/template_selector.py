"""Lists master templates in Drive and asks Claude to pick the best match."""

import json

import anthropic

import config

MODEL = "claude-opus-5"

SELECTION_TOOL = {
    "name": "select_template",
    "description": "Choose the best-matching proposal template for this client.",
    "input_schema": {
        "type": "object",
        "properties": {
            "template_id": {"type": "string", "description": "Drive file ID of the chosen template"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reasoning": {"type": "string", "description": "One or two sentences on why this template fits"},
        },
        "required": ["template_id", "confidence", "reasoning"],
    },
}


def list_templates(drive) -> list[dict]:
    """Returns [{id, title}, ...] for files in the templates folder whose name
    contains "template" (case-insensitive) anywhere in the title — the real
    naming convention in use varies ("Template of X", "X TEMPLATE Y", etc.)
    rather than following a fixed prefix."""
    templates = []
    page_token = None
    while True:
        resp = (
            drive.files()
            .list(
                q=(
                    f"'{config.TEMPLATES_DRIVE_FOLDER_ID}' in parents "
                    f"and mimeType = 'application/vnd.google-apps.presentation' "
                    f"and trashed = false"
                ),
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files", []):
            if "template" in f["name"].lower():
                templates.append({"id": f["id"], "title": f["name"]})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return templates


def pick_template(drive, ocr_fields: dict) -> dict:
    templates = list_templates(drive)
    if not templates:
        raise RuntimeError("No files containing 'template' found in templates folder")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[SELECTION_TOOL],
        tool_choice={"type": "tool", "name": "select_template"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Pick the best-matching proposal template for this client from the "
                    "list below.\n\n"
                    f"Available templates:\n{json.dumps(templates, indent=2)}\n\n"
                    f"Client context extracted from demo notes:\n"
                    f"{json.dumps(ocr_fields, indent=2)}"
                ),
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "select_template":
            result = dict(block.input)
            result["template_title"] = next(
                (t["title"] for t in templates if t["id"] == result["template_id"]), None
            )
            return result
    raise RuntimeError("Claude did not return the expected select_template tool call")
