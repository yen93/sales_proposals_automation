"""Orchestrates the 6-step sales proposal pipeline for every unprocessed
matching email found on a single run."""

import logging
import mimetypes
from typing import Optional

import config
from src import drive_service, fathom_service, form_intake_service, gmail_service, logo_service, ocr_service, slides_rewriter, supabase_service, template_selector
from src.google_clients import GoogleClients

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")

# Pilot restriction on the Form intake path: only proposals for this type are
# auto-generated for now, so we can validate the new trigger against one
# template before opening it up to the others. Not a limitation of the Gmail
# path, which has no such gate.
FORM_ALLOWED_PROPOSAL_TYPE = "uncharted ice"


def _notify_error(gmail, thread_id: str, message_id: str, subject: str, reason: str) -> None:
    try:
        gmail_service.reply_in_thread(gmail, thread_id, message_id, subject, reason)
    except Exception:
        log.exception("Failed to send error notification for message %s", message_id)


def _mark_processed(supabase, source: str, dedup_key: str, status: str, error_message=None, proposal_link=None) -> None:
    """source is "email" (Gmail message_id) or "form" (Forms response_id) —
    each dedups against its own Supabase table so the two trigger sources
    never collide."""
    if source == "email":
        supabase_service.mark_processed(supabase, dedup_key, status, error_message, proposal_link)
    else:
        supabase_service.mark_response_processed(supabase, dedup_key, status, error_message, proposal_link)


def _process_demo_notes(
    clients: GoogleClients, supabase, source: str, dedup_key: str,
    image_bytes: bytes, mime_type: str, prefill_fields: Optional[dict] = None,
) -> None:
    """Shared core for both trigger sources: OCR through final notification +
    audit log. `source`/`dedup_key` identify which Supabase table/row to
    mark; `prefill_fields` (Form-typed client_org/contact_name/event_date)
    override the OCR guess for that field when non-empty."""
    gmail, drive, slides = clients.gmail, clients.drive, clients.slides

    # Step 2 runs before Step 1's send so the notification subject can carry
    # the real client/service names, per the spec's subject line format —
    # the two steps are reordered from the spec's literal listing for that
    # reason, but the effect (an early "started" notification) is preserved.
    ocr_fields = ocr_service.extract_fields(image_bytes, mime_type)
    for field, value in (prefill_fields or {}).items():
        if value:
            ocr_fields[field] = value

    client_org_unclear = not ocr_fields.get("client_org") or "client_org" in ocr_fields.get("unclear_fields", [])
    client_org = ocr_fields.get("client_org") or "Unknown Client"
    proposal_type = ocr_fields.get("recommended_service") or "Proposal"
    subject = f"Automation: {proposal_type} for {client_org}"

    thread = gmail_service.start_notification_thread(
        gmail,
        subject=subject,
        body_text=(
            f"Automated proposal creation for {client_org} has been started. "
            "You will be notified through this same email thread once done."
        ),
    )
    thread_id = thread["thread_id"]
    notification_message_id = thread["message_id"]

    try:
        missing = ocr_service.missing_required_fields(ocr_fields)
        if missing:
            reason = f"Could not process this proposal request — unclear/missing: {', '.join(missing)}."
            _notify_error(gmail, thread_id, notification_message_id, subject, reason)
            _mark_processed(supabase, source, dedup_key, status="error", error_message=reason)
            return

        fathom_notes = fathom_service.find_matching_notes(ocr_fields)
        if fathom_notes:
            ocr_fields["fathom_meeting_notes"] = fathom_notes

        selection = template_selector.pick_template(drive, ocr_fields)
        template_id = selection["template_id"]
        if selection["confidence"] == "low" and config.FALLBACK_TEMPLATE_ID:
            template_id = config.FALLBACK_TEMPLATE_ID

        folder = drive_service.create_client_folder(drive, client_org)

        notes_ext = mimetypes.guess_extension(mime_type) or ""
        drive_service.upload_file(
            drive, folder["folder_id"], f"{client_org} Demo Call Notes{notes_ext}",
            image_bytes, mime_type,
        )

        duplicate = drive_service.duplicate_template(
            drive, template_id, folder["folder_id"],
            new_name=f"{client_org} Proposal - {proposal_type}",
        )

        logo = logo_service.find_logo_url(client_org)
        rewrite_result = slides_rewriter.rewrite(
            slides, duplicate["file_id"], ocr_fields, logo_url=logo["logo_url"]
        )

        # Match notes: purely informational, always shown when a match was found —
        # distinct from qa_notes below, which flags things a human should verify.
        match_notes = []
        if fathom_notes:
            match_notes.append("a matching Fathom call recording was found and its notes were used as source material for this proposal")
        if logo["logo_url"] and rewrite_result["logo_replaced"]:
            match_notes.append(f"a client logo match was found (guessed from domain '{logo['domain']}') and applied to the deck")

        # Pre-send QA: never blocks sending, just flags what a human should
        # double-check before this goes out to a real client.
        qa_notes = []
        if client_org_unclear:
            qa_notes.append(
                "client organisation could not be reliably identified from the notes — "
                'this proposal was created under the placeholder name "Unknown Client"; '
                "please confirm the real client name and rename the folder/deck before sending"
            )
        if not logo["logo_url"]:
            qa_notes.append("no client logo could be guessed automatically — add one manually if needed")
        elif not rewrite_result["logo_replaced"]:
            qa_notes.append("a guessed client logo could not be placed on the slides")
        else:
            qa_notes.append(
                f"client logo was auto-guessed from domain '{logo['domain']}' — "
                f"verify it's actually {client_org}'s logo before sending"
            )
        if rewrite_result["overflow_risk_ids"]:
            qa_notes.append(
                f"{len(rewrite_result['overflow_risk_ids'])} slide text box(es) may overflow their layout"
            )

        ready_message = f"The proposal for {client_org} is ready: {duplicate['view_url']}"
        if match_notes:
            ready_message += "\n\nFYI — " + "; ".join(match_notes) + "."
        if qa_notes:
            ready_message += (
                "\n\nNote: please double-check this deck before sharing externally — "
                + "; ".join(qa_notes) + "."
            )

        gmail_service.reply_in_thread(gmail, thread_id, notification_message_id, subject, ready_message)
        _mark_processed(
            supabase, source, dedup_key,
            status="needs_review" if qa_notes else "success",
            proposal_link=duplicate["view_url"],
            error_message="; ".join(qa_notes) if qa_notes else None,
        )
        log.info(
            "Processed %s:%s -> %s%s", source, dedup_key, duplicate["view_url"],
            " (needs review: " + "; ".join(qa_notes) + ")" if qa_notes else "",
        )

    except Exception as exc:
        log.exception("Pipeline failed for %s:%s", source, dedup_key)
        _notify_error(
            gmail, thread_id, notification_message_id, subject,
            f"Automation hit an unexpected error and could not finish this proposal: {exc}",
        )
        _mark_processed(supabase, source, dedup_key, status="error", error_message=str(exc))


def process_email(clients: GoogleClients, supabase, message_id: str, attachment: dict) -> None:
    image_bytes = gmail_service.download_attachment(clients.gmail, message_id, attachment["attachment_id"])
    _process_demo_notes(clients, supabase, "email", message_id, image_bytes, attachment["mime_type"])


def process_form_response(clients: GoogleClients, supabase, response: dict) -> None:
    image_bytes = form_intake_service.download_response_file(clients.drive, response["file_id"])
    prefill_fields = {k: response.get(k) for k in ("client_org", "contact_name", "event_date")}
    _process_demo_notes(
        clients, supabase, "form", response["response_id"],
        image_bytes, response["mime_type"], prefill_fields=prefill_fields,
    )


def run_once() -> None:
    clients = GoogleClients()
    supabase = supabase_service.get_client()

    candidates = gmail_service.search_matching_emails(clients.gmail)
    log.info("Found %d matching email(s)", len(candidates))

    for candidate in candidates:
        message_id = candidate["message_id"]

        if supabase_service.is_processed(supabase, message_id):
            continue

        attachment = gmail_service.get_image_attachment(clients.gmail, message_id)
        if not attachment:
            log.info("Skipping %s: no image attachment found", message_id)
            continue

        process_email(clients, supabase, message_id, attachment)

    if config.INTAKE_FORM_ID:
        form_responses = form_intake_service.list_new_responses(clients.forms)
        log.info("Found %d form response(s)", len(form_responses))

        for response in form_responses:
            if (response.get("proposal_type") or "").strip().lower() != FORM_ALLOWED_PROPOSAL_TYPE:
                log.info(
                    "Skipping form response %s: proposal_type %r is not %r",
                    response["response_id"], response.get("proposal_type"), FORM_ALLOWED_PROPOSAL_TYPE,
                )
                continue
            if supabase_service.is_response_processed(supabase, response["response_id"]):
                continue
            process_form_response(clients, supabase, response)
