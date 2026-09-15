"""Rewrites a duplicated deck's client-specific text via an LLM, and swaps
the client logo into any image shape tagged as a logo placeholder.

The rewrite is *format-preserving*: a plain delete+insert would silently drop
every run/paragraph style (text colour, font, bold, and — because Slides list
glyphs inherit the colour of their paragraph's first run — bullet colour), so
for each shape that actually changes we capture its dominant text style before
deleting and re-apply it after inserting. Shapes whose styling is *per-word*
(e.g. a header where only one word is highlighted) can't be reconstructed from
a single captured style, so those are excluded from rewriting entirely — either
because the template author tagged them (see PRESERVE_TAG_KEYWORDS) or because
the model returned the text unchanged."""

import json
import logging

from src import llm_client

log = logging.getLogger("slides_rewriter")

LOGO_TAG_KEYWORDS = ("logo", "client_logo", "client logo")

# Shapes whose alt-text (title/description) contains any of these are never
# rewritten, so their exact template formatting — including per-word highlights
# a uniform re-style can't reproduce — is preserved verbatim. "do-not-rewrite"
# is the canonical tag; the rest are lenient synonyms. Verify a template's tags
# with inspect_template.py (it prints each shape's title/desc).
PRESERVE_TAG_KEYWORDS = ("do-not-rewrite", "do_not_rewrite", "preserve", "no-rewrite")

# Text-style fields copied verbatim from the presentation `get` response into an
# updateTextStyle request (the JSON shapes match). bold/italic are handled
# separately (they have well-defined False defaults).
STYLE_FIELDS = ("foregroundColor", "backgroundColor", "fontFamily", "weightedFontFamily", "fontSize")

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


def _is_preserved_shape(element: dict) -> bool:
    """True when the shape is tagged (via alt-text title/description) to be left
    unrewritten. Mirrors find_logo_placeholders' label-matching."""
    label = f"{element.get('title', '')} {element.get('description', '')}".lower()
    return any(keyword in label for keyword in PRESERVE_TAG_KEYWORDS)


def _normalize(text: str) -> str:
    """Collapse whitespace and casefold, so a model echo that only differs in
    spacing/case counts as unchanged and skips the destructive rewrite."""
    return " ".join(text.split()).casefold()


def _first_meaningful_run(text_elements: list) -> dict:
    """The textRun whose style should drive the shape's dominant style: the
    first run with visible (non-whitespace) content, else the first run with any
    content, else None (paragraphMarker-only shape)."""
    fallback = None
    for te in text_elements:
        run = te.get("textRun")
        if run is None or "content" not in run:
            continue
        if fallback is None:
            fallback = run
        if run["content"].strip():
            return run
    return fallback


def _capture_dominant_style(element: dict) -> tuple:
    """Returns (style_dict, fields_mask) capturing the shape's dominant text
    style for re-application after a delete/insert rewrite, or (None, None) when
    there's no run to read. Structured fields are copied verbatim (the get-JSON
    shape is exactly what updateTextStyle expects, themeColor included); a
    missing field is omitted from both the style and the mask so it isn't reset.
    bold/italic are always included with a False default."""
    text_elements = element.get("shape", {}).get("text", {}).get("textElements", [])
    run = _first_meaningful_run(text_elements)
    if run is None:
        return None, None
    src = run.get("style", {})
    style = {}
    fields = []
    for field in STYLE_FIELDS:
        value = src.get(field)
        if value not in (None, {}):
            style[field] = value
            fields.append(field)
    for boolean in ("bold", "italic"):
        style[boolean] = src.get(boolean, False)
        fields.append(boolean)
    return style, ",".join(fields)


def extract_text_shapes(presentation: dict) -> list[dict]:
    """Returns [{object_id, text, has_bullets, font_size, preserve, style,
    style_fields}, ...] for every non-empty text shape. `has_bullets` tracks
    whether the original paragraph(s) were bullet-formatted so that formatting
    can be reapplied after the delete/insert rewrite below (which otherwise
    wipes it). `font_size` (may be None) backs the overflow-mitigation font
    shrink. `preserve` marks tag-excluded shapes. `style`/`style_fields` (may be
    None) capture the shape's dominant text style so it can be restored after
    the rewrite."""
    shapes = []
    for element in _iter_page_elements(presentation):
        if "shape" not in element:
            continue
        text = shape_text(element)
        if text:
            style, style_fields = _capture_dominant_style(element)
            shapes.append({
                "object_id": element["objectId"],
                "text": text,
                "has_bullets": _shape_has_bullets(element),
                "font_size": _shape_font_size(element),
                "preserve": _is_preserved_shape(element),
                "style": style,
                "style_fields": style_fields,
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


def _build_style_request(object_id: str, style: dict, fields: str) -> dict:
    if not style or not fields:
        return None
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": style,
            "fields": fields,
        }
    }


def _build_rewrite_requests(shapes: list[dict], ocr_fields: dict) -> tuple:
    # Only non-preserved shapes are sent to the model, and only their id+text —
    # the captured style/preserve metadata stays local (out of the prompt).
    llm_shapes = [
        {"object_id": s["object_id"], "text": s["text"]}
        for s in shapes
        if not s["preserve"]
    ]
    preserved_count = sum(1 for s in shapes if s["preserve"])

    content = [
        {
            "type": "input_text",
            "text": (
                "This is a sales proposal template originally written for a "
                "different client. Rewrite each shape's text below so it fits "
                "the new client, using the demo-call notes as source material. "
                "Preserve each shape's structure (bullet points, headers, "
                "length/tone) — only change client-specific content (names, "
                "org details, dates, references to the old client's situation). "
                "Leave shapes that aren't client-specific (e.g. generic section "
                "titles, footer boilerplate) unchanged — still return them. "
                "Any shape containing bracketed placeholder tokens (e.g. [CLIENT], "
                "[CLIENT_NAME], [EVENT NAME]) must have every token replaced with "
                "the real value — never leave literal brackets in the output. "
                "The cover slide's title (the first slide's main heading, if it "
                "names the generic program/keynote rather than the client — e.g. "
                "\"CROSSING THE DITCH\") should be personalized to name both the "
                "client and the program, e.g. \"{CLIENT} x {PROGRAM NAME}\". "
                "Each shape's box is sized for its original text — keep new_text's "
                "character count close to the original 'text' length for that same "
                "shape (roughly within 10-15%) so it doesn't overflow the box; "
                "shorten or trim detail rather than exceeding that.\n\n"
                f"Demo notes:\n{json.dumps(ocr_fields, indent=2)}\n\n"
                f"Template shapes:\n{json.dumps(llm_shapes, indent=2)}"
            ),
        }
    ]
    rewritten = llm_client.call_tool(content, REWRITE_TOOL)["shapes"]

    shapes_by_id = {s["object_id"]: s for s in shapes}
    requests = []
    rewritten_lengths = {}
    rewritten_count = 0
    unchanged_count = 0
    for shape in rewritten:
        object_id = shape["object_id"]
        new_text = shape["new_text"]
        original = shapes_by_id.get(object_id, {})
        # A preserved shape shouldn't reach here (never sent to the model), but
        # guard anyway; an unchanged echo skips the destructive delete/insert.
        if original.get("preserve"):
            continue
        if _normalize(new_text) == _normalize(original.get("text", "")):
            unchanged_count += 1
            continue

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
            # Restore the captured style AFTER re-bulleting so it re-asserts run
            # colour/font (and thus the inherited bullet colour).
            style_request = _build_style_request(
                object_id, original.get("style"), original.get("style_fields")
            )
            if style_request:
                requests.append(style_request)
            # Overflow shrink goes LAST so its fontSize wins over the restored one.
            shrink_request = _build_shrink_request(object_id, original, new_text)
            if shrink_request:
                requests.append(shrink_request)
        rewritten_lengths[object_id] = len(new_text)
        rewritten_count += 1

    counts = {
        "rewritten": rewritten_count,
        "preserved": preserved_count,
        "unchanged": unchanged_count,
    }
    return requests, rewritten_lengths, counts


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
    """Step 5 (part 2): rewrites text (restoring each rewritten shape's captured
    style, and shrinking font size on shapes whose new text runs notably longer
    than the original, since the Slides API has no working autofit) and swaps the
    logo. Returns {text_shapes_updated, preserved_shapes, unchanged_shapes,
    logo_replaced, overflow_risk_ids} for the pre-send QA check in pipeline.py —
    text_shapes_updated is the count of shapes actually rewritten;
    preserved_shapes were tag-excluded and unchanged_shapes were echoed
    identically (both keep their template formatting untouched);
    overflow_risk_ids flags shapes so much longer than the original that a font
    shrink alone may not be enough.

    The text rewrite and the logo swap are deliberately sent as two separate
    batchUpdate calls, not one. logo_url is only ever a guessed, unverified
    domain (see logo_service.py) — the Slides API only discovers it's
    unusable when it tries to fetch it, and batchUpdate is all-or-nothing,
    so a bad logo URL bundled into the same call would silently roll back
    every text rewrite too, leaving the deck duplicated but unedited. The
    logo call is isolated and non-fatal so a bad guess only costs the logo,
    never the text."""
    presentation = slides.presentations().get(presentationId=file_id).execute()

    text_shapes = extract_text_shapes(presentation)
    if text_shapes:
        text_requests, rewritten_lengths, counts = _build_rewrite_requests(text_shapes, ocr_fields)
    else:
        text_requests, rewritten_lengths, counts = [], {}, {"rewritten": 0, "preserved": 0, "unchanged": 0}

    if text_requests:
        slides.presentations().batchUpdate(
            presentationId=file_id, body={"requests": text_requests}
        ).execute()

    logo_replaced = False
    if logo_url:
        logo_ids = find_logo_placeholders(presentation)
        if logo_ids:
            try:
                slides.presentations().batchUpdate(
                    presentationId=file_id,
                    body={"requests": _build_logo_requests(logo_ids, logo_url)},
                ).execute()
                logo_replaced = True
            except Exception:
                log.exception(
                    "Guessed logo URL %s could not be applied to %s; leaving placeholder",
                    logo_url, file_id,
                )

    overflow_risk_ids = [
        shape["object_id"] for shape in text_shapes
        if rewritten_lengths.get(shape["object_id"], 0) > len(shape["text"]) * FLAG_RATIO
    ]

    return {
        "text_shapes_updated": counts["rewritten"],
        "preserved_shapes": counts["preserved"],
        "unchanged_shapes": counts["unchanged"],
        "logo_replaced": logo_replaced,
        "overflow_risk_ids": overflow_risk_ids,
    }
