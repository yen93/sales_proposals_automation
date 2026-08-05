"""Best-guess client logo URL, with no network call of our own.

History: previously used logo.clearbit.com (discontinued, dead as of
2026-08), then Google's favicon endpoint validated by a DuckDuckGo probe
first. Both of those did their own outbound GET from this process to decide
hit vs. miss, and both broke silently in the scheduled routine's
network-restricted sandbox: Google's endpoint 301-redirects to a sharded
t0-t3.gstatic.com host that varies per request, and when that was swapped
for DuckDuckGo's single stable host, *that* host turned out to need its own
allowlist entry too — an unreliable, ever-shifting list of hosts to keep
allowlisted.

This version makes no outbound call at all: it just builds a Google favicon
URL from the highest-priority guessed domain and returns it unvalidated.
That URL is only ever fetched by Slides' replaceImage, server-side on
Google's own infrastructure when slides_rewriter.py applies it — never by
this process — so it works regardless of this process's network access.
The cost is that this is now a guess, not a confirmed match: the caller
should always surface it to a human for verification rather than treating
a returned URL as a confirmed hit."""

import re

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
    """Returns {logo_url, domain} for the highest-priority guessed domain, or
    {logo_url: None, domain: None} if client_org has no alphanumeric
    characters to guess from at all. Never raises. The URL is an unverified
    guess, not a confirmed match — callers should flag it for human review
    rather than treating it as ground truth."""
    domains = _guess_domains(client_org)
    if not domains:
        return {"logo_url": None, "domain": None}
    domain = domains[0]
    return {
        "logo_url": f"https://www.google.com/s2/favicons?domain={domain}&sz=256",
        "domain": domain,
    }
