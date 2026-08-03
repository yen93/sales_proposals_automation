"""Debug tool: dumps a presentation's text/image element metadata so you can
identify which image shape (if any) is the logo placeholder before wiring
that up in slides_rewriter.py.

Usage:
    python inspect_template.py <presentation_id_or_url>
"""

import re
import sys

from src.google_clients import GoogleClients
from src.slides_rewriter import shape_text

_ID_RE = re.compile(r"/presentation/d/([a-zA-Z0-9_-]+)")


def _resolve_id(arg: str) -> str:
    match = _ID_RE.search(arg)
    return match.group(1) if match else arg


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    presentation_id = _resolve_id(sys.argv[1])
    clients = GoogleClients()
    presentation = clients.slides.presentations().get(presentationId=presentation_id).execute()

    for slide_index, slide in enumerate(presentation.get("slides", []), start=1):
        print(f"\n=== Slide {slide_index} ({slide['objectId']}) ===")
        for element in slide.get("pageElements", []):
            object_id = element["objectId"]
            title = element.get("title", "")
            description = element.get("description", "")
            if "shape" in element:
                text = shape_text(element)
                preview = (text[:80] + "...") if len(text) > 80 else text
                if preview:
                    print(f"  [shape] {object_id} title={title!r} desc={description!r} text={preview!r}")
            elif "image" in element:
                size = element.get("size", {})
                print(
                    f"  [image] {object_id} title={title!r} desc={description!r} "
                    f"size={size}"
                )


if __name__ == "__main__":
    main()
