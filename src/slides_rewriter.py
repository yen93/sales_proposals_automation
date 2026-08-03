"""Rewrites a duplicated deck's client-specific text via Claude, and swaps
the client logo into any image shape tagged as a logo placeholder."""

import json

import anthropic

import config

MODEL = "claude-opus-5"
LOGO_TAG_KEYWORDS = ("logo", "client_logo", "client logo")

REWRITE_TOOL = {
    "name": "rewrite_slide_text",
    "description": "Rewritten text for each editable shape in the proposal deck, tailored to the new client.",
    "input_schema": {
        "type": "object",
        "properties": {
            "shapes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "object_id": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["object_id", "new_text"],
                },
            }
        },
        "required": ["shapes"],
    },
}


def _iter_page_elements(presentation: dict):
    for slide in presentation.get("slides", []):
        for element in slide.get("pageElements", []):
            yield element


def shape_text(element: dict) -> str:
    text_elements = element.get("shape", {}).get("text", {}).get("textElements", [])
    return "".join(
        te.get("textRun", {}).get("content", "") for te in text_elements
    ).strip()


def _shape_has_bullets(element: dict) -> bool:
    text_elements = element.get("shape", {}).get("text", {}).get("textElements", [])
    return any("bullet" in te.get("paragraphMarker", {}) for te in text_elements)


def _shape_font_size(element: dict) -> float:
    text_elements = element.get("shape", {}).get("text", {}).get("textElements", [])
    for te in text_elements:
        size = te.get("textRun", {}).get("style", {}).get("fontSize", {}).get("magnitude")
        if size:
            return size
    return None


def extract_text_shapes(presentation: dict) -> list[dict]:
    """Returns [{object_id, text, has_bullets, font_size}, ...] for every
    non-empty text shape. `has_bullets` tracks whether the original template
    paragraph(s) were bullet-formatted, so that formatting can be reapplied
    after the delete/insert rewrite below (which otherwise wipes it).
    `font_size` (may be None) backs the overflow-mitigation font shrink."""
    shapes = []
    for element in _iter_page_elements(presentation):
        if "shape" not in element:
            continue
        text = shape_text(element)
        if text:
            shapes.append({
                "object_id": element["objectId"],
                "text": text,
                "has_bullets": _shape_has_bullets(element),
                "font_size": _shape_font_size(element),
            })
    return shapes


def find_logo_placeholders(presentation: dict) -> list[str]:
    """Returns objectIds of image shapes whose title/description marks them
    as the client-logo placeholder. Depends on the template author having
    tagged the shape's alt text (see inspect_template.py to verify a given
    template actually has one)."""
    logo_ids = []
    for element in _iter_page_elements(presentation):
        if "image" not in element:
            continue
        label = f"{element.get('title', '')} {element.get('description', '')}".lower()
        if any(keyword in label for keyword in LOGO_TAG_KEYWORDS):
            logo_ids.append(element["objectId"])
    return logo_ids


def _build_rewrite_requests(shapes: list[dict], ocr_fields: dict) -> list[dict]:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        tools=[REWRITE_TOOL],
        tool_choice={"type": "tool", "name": "rewrite_slide_text"},
        messages=[
            {
                "role": "user",
                "content": (
                    "This is a sales proposal template originally written for a "
                    "different client. Rewrite each shape's text below so it fits "
                    "the new client, using the demo-call notes as source material. "
                    "Preserve each shape's structure (bullet points, headers, "
                    "length/tone) — only change client-specific content (names, "
                    "org details, dates, references to the old client's situation). "
                    "Leave shapes that aren't client-specific (e.g. generic section "
                    "titles, footer boilerplate) unchanged — still return them. "
                    "Each shape's box is sized for its original text — keep new_text's "
                    "character count close to the original 'text' length for that same "
                    "shape (roughly within 10-15%) so it doesn't overflow the box; "
                    "shorten or trim detail rather than exceeding that.\n\n"
                    f"Demo notes:\n{json.dumps(ocr_fields, indent=2)}\n\n"
                    f"Template shapes:\n{json.dumps(shapes, indent=2)}"
                ),
            }
        ],
    )

    rewritten = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "rewrite_slide_text":
            rewritten = block.input["shapes"]
    if rewritten is None:
        raise RuntimeError("Claude did not return the expected rewrite_slide_text tool call")

    shapes_by_id = {s["object_id"]: s for s in shapes}
    requests = []
    rewritten_lengths = {}
    for shape in rewritten:
        object_id = shape["object_id"]
        new_text = shape["new_text"]
        original = shapes_by_id.get(object_id, {})
        requests.append({"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}})
        if new_text:
            requests.append({"insertText": {"objectId": object_id, "insertionIndex": 0, "text": new_text}})
            if original.get("has_bullets"):
                requests.append({
                    "createParagraphBullets": {
                        "objectId": object_id,
                        "textRange": {"type": "ALL"},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                })
            shrink_request = _build_shrink_request(object_id, original, new_text)
            if shrink_request:
                requests.append(shrink_request)
        rewritten_lengths[object_id] = len(new_text)
    return requests, rewritten_lengths


SHRINK_TRIGGER_RATIO = 1.15
MIN_FONT_SCALE = 0.7


def _build_shrink_request(object_id: str, original: dict, new_text: str) -> dict:
    """The Slides API doesn't support enabling autofit/shrink-to-fit via
    batchUpdate ("Autofit types other than NONE are not supported") — the
    only real lever is directly reducing font size when rewritten text runs
    meaningfully longer than what the shape's box was designed to hold."""
    original_len = len(original.get("text", ""))
    font_size = original.get("font_size")
    if not original_len or not font_size or len(new_text) <= original_len * SHRINK_TRIGGER_RATIO:
        return None
    scale = max(original_len / len(new_text), MIN_FONT_SCALE)
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {"fontSize": {"magnitude": round(font_size * scale, 1), "unit": "PT"}},
            "fields": "fontSize",
        }
    }


def _build_logo_requests(logo_ids: list[str], logo_url: str) -> list[dict]:
    return [
        {
            "replaceImage": {
                "imageObjectId": object_id,
                "imageReplaceMethod": "CENTER_INSIDE",
                "url": logo_url,
            }
        }
        for object_id in logo_ids
    ]


FLAG_RATIO = 1.5


def rewrite(slides, file_id: str, ocr_fields: dict, logo_url: str = None) -> dict:
    """Step 5 (part 2): rewrites text (shrinking font size on shapes whose new
    text runs notably longer than the original, since the Slides API has no
    working autofit) and swaps the logo, in one batchUpdate. Returns
    {text_shapes_updated, logo_replaced, overflow_risk_ids} for the pre-send
    QA check in pipeline.py — overflow_risk_ids flags shapes so much longer
    than the original that a font shrink alone may not be enough."""
    presentation = slides.presentations().get(presentationId=file_id).execute()

    text_shapes = extract_text_shapes(presentation)
    if text_shapes:
        requests, rewritten_lengths = _build_rewrite_requests(text_shapes, ocr_fields)
    else:
        requests, rewritten_lengths = [], {}

    logo_replaced = False
    if logo_url:
        logo_ids = find_logo_placeholders(presentation)
        if logo_ids:
            requests.extend(_build_logo_requests(logo_ids, logo_url))
            logo_replaced = True

    if requests:
        slides.presentations().batchUpdate(
            presentationId=file_id, body={"requests": requests}
        ).execute()

    overflow_risk_ids = [
        shape["object_id"] for shape in text_shapes
        if rewritten_lengths.get(shape["object_id"], 0) > len(shape["text"]) * FLAG_RATIO
    ]

    return {
        "text_shapes_updated": len(text_shapes),
        "logo_replaced": logo_replaced,
        "overflow_risk_ids": overflow_risk_ids,
    }
