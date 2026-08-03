Summary:

Project layout (sales_proposals_automation/):
- config.py — loads .env + project_vars.txt
- oauth_setup.py — one-time Google OAuth consent flow, saves refresh token
- inspect_template.py — debug tool to dump a template's shape/image IDs (use this to find/tag the logo placeholder in each TEMPLATE_* deck)
- pipeline.py / main.py — orchestrates the 6 steps end-to-end, safe to rerun on a schedule
- src/: google_clients.py, gmail_service.py, supabase_service.py, ocr_service.py (Claude vision), template_selector.py (Claude picks the template), drive_service.py, logo_service.py (Clearbit), slides_rewriter.py (Claude rewrites slide text + swaps logo)

All files pass a syntax check. Two things to do before it can run for real:

1. Fill in .env (copy from .env.example): your Google OAuth client ID/secret, Supabase URL/service key, and a new Anthropic API key (console.anthropic.com — you said this one isn't set up yet). Then run pip install -r requirements.txt and python oauth_setup.py once.
2. Confirm/add Supabase columns: the plan assumes proposal_demo_notes_email_logs has status, error_message, proposal_link, processed_at alongside email_id/is_processed — let me know your actual schema if it's narrower and I'll adjust supabase_service.py.

After that, follow the plan's verification steps (send yourself a matching test email, run python main.py manually, confirm the folder/deck/notifications appear) before wiring up the /schedule cron job.