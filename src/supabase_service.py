"""Dedup tracking against the proposal_demo_notes_email_logs Supabase table.

Actual columns:
    id             uuid primary key
    created_at     timestamptz
    message_id     text  -- Gmail message id
    is_processed   boolean
    status         text        -- 'success' | 'error' | 'needs_review'
    error_message  text, nullable
    proposal_link  text, nullable
    processed_at   timestamptz, nullable
"""

from datetime import datetime, timezone
from typing import Optional

from supabase import Client, create_client

import config


def get_client() -> Client:
    config.require("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)


def is_processed(client: Client, message_id: str) -> bool:
    resp = (
        client.table(config.SUPABASE_LOG_TABLE)
        .select("message_id")
        .eq("message_id", message_id)
        .eq("is_processed", True)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def mark_processed(
    client: Client,
    message_id: str,
    status: str,
    error_message: Optional[str] = None,
    proposal_link: Optional[str] = None,
) -> None:
    """Marks the email as processed regardless of success/error outcome so a
    bad email is never retried forever. `status` distinguishes the outcome."""
    client.table(config.SUPABASE_LOG_TABLE).upsert(
        {
            "message_id": message_id,
            "is_processed": True,
            "status": status,
            "error_message": error_message,
            "proposal_link": proposal_link,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="message_id",
    ).execute()
