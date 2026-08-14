---
name: run-proposal-pipeline
description: Run the sales proposal automation pipeline once, on demand — the same routine the hourly cloud schedule (trig_01KNnjdscJ1KpMTEKqneqs1S) runs. Scans Gmail for matching demo-note emails, OCRs the attachment, picks a template, builds the Drive folder + Slides deck, replaces the logo, and sends the Gmail notification, skipping anything already marked processed in Supabase. Use when the user asks to run/trigger/kick off the proposal pipeline manually, process new demo-note emails right now, or test the automation locally.
---

# Run proposal pipeline

Executes `pipeline.run_once()` (same code path as the scheduled routine) against
this local checkout, using the local `.env` and `project_vars.txt`.

## Steps

1. Confirm `.env` exists in the project root. If it's missing, stop and tell the
   user to run `oauth_setup.py` first (see `.env.example` for the required vars) —
   don't attempt to fabricate credentials.
2. Run the pipeline from the project root:
   ```
   python main.py
   ```
3. Read the log output (`pipeline` logger, INFO level). It reports:
   - how many matching emails were found (`Found %d matching email(s)`)
   - one line per processed message: the proposal link, or `needs review` with
     the reason (e.g. unverified logo guess, slide overflow risk), or that it
     was skipped (already processed / no image attachment).
4. Summarize the run for the user: counts found/processed/skipped, any
   `needs_review` or error outcomes with their reasons, and the Slides links
   produced. Don't just paste the raw log.
5. If it fails on a network call to Supabase or Fathom, that's unexpected when
   run locally — those hosts are only blocked in the cloud sandbox's network
   policy (see project memory `network_policy_supabase_egress`), not from a
   normal terminal. Treat a local 403/timeout as a real error worth
   investigating, not the known cloud-only egress issue.
6. If a `ModuleNotFoundError` occurs, run `pip install -r requirements.txt` and
   retry once.

## Notes

- Safe to re-run any time: Supabase dedup (`proposal_demo_notes_email_logs`)
  skips any email already marked processed, so running this manually between
  scheduled fires won't double-send proposals.
- This does not touch the cloud schedule itself — it's a local, immediate run
  of the identical pipeline code, useful for testing a fix before it's picked
  up by the next hourly fire, or for processing something without waiting.
