"""Gmail operations: finding matching demo-notes emails, pulling the attachment,
and sending the Step 1 / Step 6 notification messages in one thread."""

import base64
from email.mime.text import MIMEText
from email.utils import make_msgid
from typing import Optional

import config


def search_matching_emails(gmail) -> list[dict]:
    """Runs the trigger query and returns [{message_id, thread_id}, ...] for
    messages that actually carry a single image attachment (the query alone
    can't guarantee the attachment is an image)."""
    results = []
    page_token = None
    while True:
        resp = (
            gmail.users()
            .messages()
            .list(userId="me", q=config.GMAIL_TRIGGER_QUERY, pageToken=page_token)
            .execute()
        )
        for msg in resp.get("messages", []):
            results.append({"message_id": msg["id"], "thread_id": msg["threadId"]})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def get_image_attachment(gmail, message_id: str) -> Optional[dict]:
    """Returns {attachment_id, filename, mime_type} for the first image or PDF
    attachment on the message (e.g. a phone-scanner PDF of the notes page), or
    None if it has neither."""
    message = (
        gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    )
    parts = message.get("payload", {}).get("parts", []) or []
    for part in parts:
        filename = part.get("filename")
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        is_supported = mime_type.startswith("image/") or mime_type == "application/pdf"
        if filename and is_supported and body.get("attachmentId"):
            return {
                "attachment_id": body["attachmentId"],
                "filename": filename,
                "mime_type": mime_type,
            }
    return None


def download_attachment(gmail, message_id: str, attachment_id: str) -> bytes:
    attachment = (
        gmail.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    return base64.urlsafe_b64decode(attachment["data"])


def _get_message_id_header(gmail, message_id: str) -> str:
    message = (
        gmail.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata", metadataHeaders=["Message-ID"])
        .execute()
    )
    for header in message.get("payload", {}).get("headers", []):
        if header["name"].lower() == "message-id":
            return header["value"]
    return ""


def _send(gmail, mime_message, thread_id: Optional[str] = None) -> dict:
    raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode("utf-8")
    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    return gmail.users().messages().send(userId="me", body=body).execute()


def start_notification_thread(gmail, subject: str, body_text: str) -> dict:
    """Step 1: sends a brand-new message (not a reply) to the notification
    recipients and returns {message_id, thread_id} for later use in Step 6."""
    mime_message = MIMEText(body_text)
    mime_message["To"] = ", ".join(config.NOTIFICATION_RECIPIENTS)
    mime_message["Subject"] = subject
    mime_message["Message-ID"] = make_msgid()

    sent = _send(gmail, mime_message)
    return {"message_id": sent["id"], "thread_id": sent["threadId"]}


def reply_in_thread(gmail, thread_id: str, original_message_id: str, subject: str, body_text: str) -> dict:
    """Step 6 (or an error reply): sends a reply within the same Gmail thread."""
    in_reply_to = _get_message_id_header(gmail, original_message_id)

    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    mime_message = MIMEText(body_text)
    mime_message["To"] = ", ".join(config.NOTIFICATION_RECIPIENTS)
    mime_message["Subject"] = reply_subject
    if in_reply_to:
        mime_message["In-Reply-To"] = in_reply_to
        mime_message["References"] = in_reply_to

    sent = _send(gmail, mime_message, thread_id=thread_id)
    return {"message_id": sent["id"], "thread_id": sent["threadId"]}
