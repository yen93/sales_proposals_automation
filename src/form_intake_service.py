"""Reads submissions from the "Demo Call Notes Intake" Google Form via the
Forms API, as an alternative trigger to the Gmail subject-line convention.

The Form must have items with these EXACT titles (case-sensitive) for the
field mapping below to find them — text answers are optional prefills that
override the OCR guess for that field when present; the file-upload item is
required, since it's what OCR actually runs against.
"""

from typing import Optional

import config

FIELD_ITEM_TITLES = {
    "client_org": "Client org",
    "contact_name": "Contact name",
    "event_date": "Event date",
    "proposal_type": "Proposal Type",
}
FILE_UPLOAD_ITEM_TITLE = "Demo call notes"


def _build_question_id_map(forms_client) -> dict:
    """Maps each expected item title -> its questionId, by inspecting the
    form's structure once. Titles not found on the form are silently
    omitted (that field just won't be prefilled)."""
    form = forms_client.forms().get(formId=config.INTAKE_FORM_ID).execute()
    title_to_question_id = {}
    for item in form.get("items", []):
        title = item.get("title", "")
        question_item = item.get("questionItem", {})
        question_id = question_item.get("question", {}).get("questionId")
        if question_id:
            title_to_question_id[title] = question_id
    return title_to_question_id


def list_new_responses(forms_client) -> list[dict]:
    """Returns [{response_id, client_org, contact_name, event_date,
    proposal_type, file_id, filename, mime_type}, ...] for every response
    that has a file-upload answer. Callers are responsible for the Supabase
    dedup check AND the proposal_type gate (pipeline.run_once() only
    processes "Uncharted Ice") — this always lists ALL responses on the
    form (fine at this form's expected volume; Forms API supports a
    timestamp filter if that stops being true).
    """
    if not config.INTAKE_FORM_ID:
        return []

    title_to_question_id = _build_question_id_map(forms_client)
    file_question_id = title_to_question_id.get(FILE_UPLOAD_ITEM_TITLE)
    if not file_question_id:
        raise RuntimeError(
            f'Intake form has no item titled "{FILE_UPLOAD_ITEM_TITLE}" — '
            "check the form matches src/form_intake_service.py's expected titles."
        )

    field_question_ids = {
        field: title_to_question_id[title]
        for field, title in FIELD_ITEM_TITLES.items()
        if title in title_to_question_id
    }

    results = []
    page_token = None
    while True:
        resp = (
            forms_client.forms()
            .responses()
            .list(formId=config.INTAKE_FORM_ID, pageToken=page_token)
            .execute()
        )
        for response in resp.get("responses", []):
            answers = response.get("answers", {})
            file_answer = answers.get(file_question_id, {}).get("fileUploadAnswers", {})
            files = file_answer.get("answers", [])
            if not files:
                continue  # required question, but be defensive about drafts/partial responses

            first_file = files[0]
            entry = {
                "response_id": response["responseId"],
                "file_id": first_file["fileId"],
                "filename": first_file.get("fileName", ""),
                "mime_type": first_file.get("mimeType", "application/octet-stream"),
            }
            for field, question_id in field_question_ids.items():
                text_answers = answers.get(question_id, {}).get("textAnswers", {}).get("answers", [])
                entry[field] = text_answers[0]["value"] if text_answers else ""
            results.append(entry)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def download_response_file(drive, file_id: str) -> bytes:
    return drive.files().get_media(fileId=file_id).execute()
