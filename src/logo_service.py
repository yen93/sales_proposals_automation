"""Best-effort client logo lookup: probes DuckDuckGo's favicon endpoint to
detect a hit, then hands back a Google favicon URL for the actual slide
embed.

History: previously used logo.clearbit.com, which was discontinued (the
domain no longer resolves at all as of 2026-08). Switched to Google's
favicon endpoint (www.google.com/s2/favicons), which works fine for the
slide embed — Slides' replaceImage fetches that URL server-side from
Google's own network, so our process's network access is irrelevant there —
but that same endpoint 301-redirects to a sharded t0-t3.gstatic.com host
that varies per request, so using it for our own pre-embed *validation* GET
is unreliable in network-restricted environments (e.g. a scheduled routine
sandbox): only the initial www.google.com hop is reachable, not the shard
the redirect lands on, so the validation GET fails even though the eventual
embed would have worked fine. DuckDuckGo's endpoint is a single stable host
with no redirect, so it's used here purely to confirm a domain has a real
favicon (it 404s rather than returning a generic fallback icon on a miss,
unlike Google's). Its own .ico response isn't used for the embed — Slides'
replaceImage rejects that format — only Google's PNG URL is returned."""

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
        probe_url = f"https://icons.duckduckgo.com/ip3/{domain}.ico"
        try:
            resp = requests.get(probe_url, timeout=10)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            embed_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=256"
            return {"logo_url": embed_url, "domain": domain, "logo_bytes": resp.content}
    return {"logo_url": None, "domain": None, "logo_bytes": None}
