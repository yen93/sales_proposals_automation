# CLAUDE.md

Guidance for a future Claude Code session working in this repo. For a full
current-state inventory (files, integrations, data stores), see
`as_built.txt` — keep that updated too when things change here.

## What this is

Automates generating a customized sales proposal (Google Slides deck) from a
photographed/scanned handwritten demo-call-notes page, triggered by a Gmail
email or a Google Form submission. See `AS_BUILT_sales_proposal_automation.txt`
for the full step-by-step architecture (Steps A-K) — that file is the
authoritative as-built spec; treat any divergence between it and the code as
a bug in that file, and update it when you change pipeline behavior.
`workflow_markdown.txt` is the *original* design-intent spec and is
deliberately left as originally written — don't edit it to match current
reality; record deviations in the AS_BUILT file instead.

## Running it

```
pip install -r requirements.txt
python main.py          # runs pipeline.run_once() once
```

Needs a populated `.env` (see `.env.example`) and `project_vars.txt` (JSON,
non-secret config — Drive folder links, notification recipients, etc.).
`oauth_setup.py` mints `GOOGLE_REFRESH_TOKEN` via a one-time interactive OAuth
flow — re-run it if `config.GOOGLE_SCOPES` ever changes, since an
already-minted token won't pick up a new scope.

There's also a `.claude/skills/run-proposal-pipeline` project skill that runs
the identical `pipeline.run_once()` path — use `/run-proposal-pipeline` to
trigger a manual run from inside Claude Code.

## LLM provider

Migrated from the Anthropic/Claude API to OpenAI on 2026-09-08 (the Anthropic
org ran out of usage credits). All 4 LLM call sites — `src/ocr_service.py`
(OCR/vision+PDF), `src/template_selector.py`, `src/fathom_service.py`,
`src/slides_rewriter.py` — go through the shared helper in
`src/llm_client.py`, which forces a single named-function tool call with
strict JSON-schema output. Each call site's `TOOL` dict is still in its
original Anthropic shape (`{name, description, input_schema}`) — `llm_client`
converts it internally, so don't "fix" that shape at the call sites.

Model is `config.OPENAI_MODEL`, overridable via the `OPENAI_MODEL` env var,
currently defaulting to `gpt-5.6-luna` (the cheapest vision-capable OpenAI
tier — chosen for cost over accuracy). **Gotcha**: `config.py` reads it as
`os.environ.get("OPENAI_MODEL") or "gpt-5.6-luna"`, not the more obvious
`.get("OPENAI_MODEL", "gpt-5.6-luna")` — a *blank* `OPENAI_MODEL=` line in
`.env` (as opposed to the line being absent) would otherwise silently send an
empty model string to the API and 404. Apply the same `or` pattern to any
other new config value you add that could plausibly be set-but-blank in
`.env`.

Since this is the cheapest tier, watch for handwriting-OCR accuracy
regressions (more `client_org`/`unclear_fields` flags than before) —
`gpt-5.6-terra` ($2/$12 per million tokens) is the documented middle ground
if that becomes a problem.

## Key conventions / gotchas

- **`client_org` is not a hard-required OCR field** (changed 2026-09-08). If
  it's missing or OCR flags it unclear, the pipeline still proceeds under the
  placeholder "Unknown Client" and surfaces a `qa_note` on the final
  notification instead of blocking. Only `recommended_service` and `summary`
  (`config.REQUIRED_OCR_FIELDS`) still hard-block a run.
- **A failed/errored request is never retried automatically** —
  `supabase_service.mark_processed()` sets `is_processed=True` regardless of
  outcome. To reprocess a specific stuck email/response, you have to
  manually delete or reset its Supabase row (or call
  `pipeline._process_demo_notes()` directly under a distinct dedup key for a
  one-off test — see the logo/slide-rewrite verification approach used in
  the 2026-09-08 OpenAI migration).
- **`logo_service.py` makes no outbound network call of its own** — it just
  guesses a Google-favicon URL from the client name and returns it
  unvalidated; only Slides' `replaceImage` (server-side, on Google's
  infrastructure) actually fetches it. This is deliberate: two earlier
  approaches that validated the URL themselves broke silently in the
  scheduled cloud routine's network-restricted sandbox. Never "fix" this by
  adding a validation fetch back in without re-reading that history in the
  as-built doc first.
- **The scheduled cloud routine's network sandbox is allowlist-restricted.**
  Any new outbound host this code calls directly (Supabase and Fathom both
  needed this) may need adding to that environment's Network Access
  allowlist — failures here are often silent where the calling code already
  catches exceptions (e.g. logo lookup, Fathom). `api.openai.com`'s
  allowlist status is currently unverified post-migration — confirm before
  trusting the hourly routine.
- **`.env.example` is tracked in git; `.env` is gitignored.** Real secrets
  belong only in `.env`. Double-check before writing to `.env.example`.
- Text/prompt content for the LLM calls (tool descriptions, rewrite
  instructions) is deliberately verbose/specific — it encodes real product
  requirements (e.g. "never leave literal brackets," "keep new_text length
  within 10-15% of original," "one-hour-each fallback keynote naming
  convention"). Don't trim it for brevity without checking why a line is
  there.

## Secrets in `.env`

`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`,
`OPENAI_API_KEY`, `OPENAI_MODEL` (optional), `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY`, `FATHOM_API_KEY` (optional), plus optional overrides
(`GMAIL_TRIGGER_QUERY`, `FALLBACK_TEMPLATE_ID`, `FATHOM_LOOKBACK_DAYS`). See
`.env.example` for the full annotated list.
