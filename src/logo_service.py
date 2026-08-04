"""Best-effort client logo lookup via Google's public favicon endpoint.

Previously used logo.clearbit.com, which was discontinued (the domain no
longer resolves at all as of 2026-08). Favicons are lower-fidelity than a
dedicated brand-logo API and this endpoint is undocumented, but it's free,
requires no signup, and is currently reliable."""

import re

import requests

_SUFFIXES = re.compile(
    r"\b(pty|ltd|limited|llc|inc|incorporated|corp|corporation|company|co|group)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

CANDIDATE_TLDS = [".com.au", ".com", ".co"]


def _guess_domains(client_org: str) -> list[str]:
    name = _SUFFIXES.sub("", client_org.lower())
    slug = _NON_ALNUM.sub("", name)
    if not slug:
        return []
    return [f"{slug}{tld}" for tld in CANDIDATE_TLDS]


def find_logo_url(client_org: str) -> dict:
    """Returns {logo_url, domain} on a hit, or {logo_url: None} if no
    candidate domain resolved to an actual logo image. Never raises —
    a miss here should not fail the whole pipeline."""
    for domain in _guess_domains(client_org):
        url = f"https://www.google.com/s2/favicons?domain={domain}&sz=256"
        try:
            resp = requests.get(url, timeout=10)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            return {"logo_url": url, "domain": domain, "logo_bytes": resp.content}
    return {"logo_url": None, "domain": None, "logo_bytes": None}
