"""Orchestrates the 6-step sales proposal pipeline for every unprocessed
matching email found on a single run."""

import logging
import mimetypes

import config
from src import drive_service, fathom_service, gmail_service, logo_service, ocr_service, slides_rewriter, supabase_service, template_selector
from src.google_clients import GoogleClients

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


def _notify_error(gmail, thread_id: str, message_id: str, subject: str, reason: str) -> None:
    try:
        gmail_service.reply_in_thread(gmail, thread_id, message_id, subject, reason)
    except Exception:
        log.exception("Failed to send error notification for message %s", message_id)


def process_email(clients: GoogleClients, supabase, message_id: str, attachment: dict) -> None:
    gmail, drive, slides = clients.gmail, clients.drive, clients.slides

    # Step 2 runs before Step 1's send so the notification subject can carry
    # the real client/service names, per the spec's subject line format —
    # the two steps are reordered from the spec's literal listing for that
    # reason, but the effect (an early "started" notification) is preserved.
    image_bytes = gmail_service.download_attachment(gmail, message_id, attachment["attachment_id"])
    ocr_fields = ocr_service.extract_fields(image_bytes, attachment["mime_type"])

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
            supabase_service.mark_processed(supabase, message_id, status="error", error_message=reason)
            return

        fathom_notes = fathom_service.find_matching_notes(ocr_fields)
        if fathom_notes:
            ocr_fields["fathom_meeting_notes"] = fathom_notes

        selection = template_selector.pick_template(drive, ocr_fields)
        template_id = selection["template_id"]
        if selection["confidence"] == "low" and config.FALLBACK_TEMPLATE_ID:
            template_id = config.FALLBACK_TEMPLATE_ID

        folder = drive_service.create_client_folder(drive, client_org)

        notes_ext = mimetypes.guess_extension(attachment["mime_type"]) or ""
        drive_service.upload_file(
            drive, folder["folder_id"], f"{client_org} Demo Call Notes{notes_ext}",
            image_bytes, attachment["mime_type"],
        )

        duplicate = drive_service.duplicate_template(
            drive, template_id, folder["folder_id"],
            new_name=f"{client_org} Proposal - {proposal_type}",
        )

        logo = logo_service.find_logo_url(client_org)
        rewrite_result = slides_rewriter.rewrite(
            slides, duplicate["file_id"], ocr_fields, logo_url=logo["logo_url"]
        )

        # Pre-send QA: never blocks sending, just flags what a human should
        # double-check before this goes out to a real client.
        qa_notes = []
        if logo["logo_url"] and not rewrite_result["logo_replaced"]:
            qa_notes.append("client logo was found but could not be placed on the slides")
        if rewrite_result["overflow_risk_ids"]:
            qa_notes.append(
                f"{len(rewrite_result['overflow_risk_ids'])} slide text box(es) may overflow their layout"
            )

        ready_message = f"The proposal for {client_org} is ready: {duplicate['view_url']}"
        if qa_notes:
            ready_message += (
                "\n\nNote: please double-check this deck before sharing externally — "
                + "; ".join(qa_notes) + "."
            )

        gmail_service.reply_in_thread(gmail, thread_id, notification_message_id, subject, ready_message)
        supabase_service.mark_processed(
            supabase, message_id,
            status="needs_review" if qa_notes else "success",
            proposal_link=duplicate["view_url"],
            error_message="; ".join(qa_notes) if qa_notes else None,
        )
        log.info(
            "Processed %s -> %s%s", message_id, duplicate["view_url"],
            " (needs review: " + "; ".join(qa_notes) + ")" if qa_notes else "",
        )

    except Exception as exc:
        log.exception("Pipeline failed for message %s", message_id)
        _notify_error(
            gmail, thread_id, notification_message_id, subject,
            f"Automation hit an unexpected error and could not finish this proposal: {exc}",
        )
        supabase_service.mark_processed(supabase, message_id, status="error", error_message=str(exc))


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
