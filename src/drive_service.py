"""Drive folder creation and template duplication (Steps 4-5)."""

from datetime import datetime

import config

FOLDER_MIME = "application/vnd.google-apps.folder"


def _escape(value: str) -> str:
    return value.replace("'", "\\'")


def create_client_folder(drive, client_org: str, year: int = None) -> dict:
    """Step 4: creates (or reuses, on a rerun) '[Client] [Year]' under the
    master proposals folder. Returns {folder_id, folder_name}."""
    year = year or datetime.now().year
    folder_name = f"{client_org} {year}"

    existing = (
        drive.files()
        .list(
            q=(
                f"'{config.PROJ_DRIVE_FOLDER_ID}' in parents "
                f"and mimeType = '{FOLDER_MIME}' "
                f"and name = '{_escape(folder_name)}' "
                f"and trashed = false"
            ),
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = existing.get("files", [])
    if files:
        return {"folder_id": files[0]["id"], "folder_name": folder_name}

    created = (
        drive.files()
        .create(
            body={
                "name": folder_name,
                "mimeType": FOLDER_MIME,
                "parents": [config.PROJ_DRIVE_FOLDER_ID],
            },
            fields="id, name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return {"folder_id": created["id"], "folder_name": folder_name}


def duplicate_template(drive, template_id: str, folder_id: str, new_name: str) -> dict:
    """Step 5 (part 1): copies the chosen template into the client's folder.
    Returns {file_id, view_url}."""
    copied = (
        drive.files()
        .copy(
            fileId=template_id,
            body={"name": new_name, "parents": [folder_id]},
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    file_id = copied["id"]
    return {
        "file_id": file_id,
        "view_url": f"https://docs.google.com/presentation/d/{file_id}/edit",
    }
