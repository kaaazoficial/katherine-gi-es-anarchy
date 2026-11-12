"""Privacy-first retention for Katherine's short-lived operational/user state.

Conversation context is never written to SQLite. This module only expires data
used by existing commands; community configuration remains intact.
"""
from __future__ import annotations

from datetime import date, timedelta


USER_INACTIVITY_DAYS = 90
PROFILE_TEXT_DAYS = 30
ACTIVITY_RETENTION_DAYS = 7
GIFT_RETENTION_DAYS = 30
GIFT_MAX_PER_RECIPIENT = 5
ADMIN_AUDIT_RETENTION_DAYS = 30
MODERATION_RETENTION_DAYS = 90
CONFIG_CREATOR_ID_RETENTION_DAYS = 90


def cleanup_database_retention(
    db,
    *,
    now: float,
    today: date,
) -> dict[str, int]:
    """Delete or anonymize old user-linked records without storing chat history.

    Existing gameplay state (points, pity, affinities) is retained only while
    the associated account is active; all of it is removed after 90 days of
    inactivity. Free-text profile fields expire after 30 days.
    """
    user_cutoff = float(now) - USER_INACTIVITY_DAYS * 86400
    profile_cutoff = float(now) - PROFILE_TEXT_DAYS * 86400
    gift_cutoff = float(now) - GIFT_RETENTION_DAYS * 86400
    audit_cutoff = float(now) - ADMIN_AUDIT_RETENTION_DAYS * 86400
    moderation_cutoff = float(now) - MODERATION_RETENTION_DAYS * 86400
    creator_cutoff = float(now) - CONFIG_CREATOR_ID_RETENTION_DAYS * 86400
    activity_cutoff = (today - timedelta(days=ACTIVITY_RETENTION_DAYS - 1)).isoformat()
    today_key = today.isoformat()
    stale_users = "SELECT uid FROM users WHERE last_seen < ?"

    counts: dict[str, int] = {}

    cursor = db.execute(
        """UPDATE users
           SET bio='', status='', profile_updated_at=NULL
           WHERE profile_updated_at IS NOT NULL
             AND profile_updated_at < ?
             AND (bio <> '' OR status <> '')""",
        (profile_cutoff,),
    )
    counts["profile_texts_expired"] = max(0, cursor.rowcount)

    # Remove user-linked state before deleting inactive account rows.
    for table, condition, params in (
        (
            "affinities",
            f"uid IN ({stale_users}) OR target_uid IN ({stale_users})",
            (user_cutoff, user_cutoff),
        ),
        (
            "gifts",
            f"sender_uid IN ({stale_users}) OR target_uid IN ({stale_users}) OR created_at < ?",
            (user_cutoff, user_cutoff, gift_cutoff),
        ),
        ("wish_state", f"uid IN ({stale_users})", (user_cutoff,)),
        ("reward_claims", f"uid IN ({stale_users})", (user_cutoff,)),
        (
            "moderation_warnings",
            f"target_uid IN ({stale_users}) OR moderator_uid IN ({stale_users}) OR created_at < ?",
            (user_cutoff, user_cutoff, moderation_cutoff),
        ),
    ):
        cursor = db.execute(f"DELETE FROM {table} WHERE {condition}", params)
        counts[f"{table}_expired"] = max(0, cursor.rowcount)

    cursor = db.execute("DELETE FROM users WHERE last_seen < ?", (user_cutoff,))
    counts["inactive_users_deleted"] = max(0, cursor.rowcount)

    # These are community rules/playlists, not conversation memory. Keep the
    # configuration, but remove the creator's user identifier after 90 days.
    cursor = db.execute(
        "UPDATE dj_playlists SET created_by='' WHERE created_at < ? AND created_by <> ''",
        (creator_cutoff,),
    )
    counts["playlist_creator_ids_anonymized"] = max(0, cursor.rowcount)
    cursor = db.execute(
        "UPDATE auto_reply_rules SET created_by='' WHERE created_at < ? AND created_by <> ''",
        (creator_cutoff,),
    )
    counts["auto_reply_creator_ids_anonymized"] = max(0, cursor.rowcount)

    cursor = db.execute("DELETE FROM chat_activity_daily WHERE day_key < ?", (activity_cutoff,))
    counts["daily_activity_expired"] = max(0, cursor.rowcount)
    cursor = db.execute("DELETE FROM chat_activity_hourly WHERE day_key < ?", (activity_cutoff,))
    counts["hourly_activity_expired"] = max(0, cursor.rowcount)

    cursor = db.execute("DELETE FROM admin_audit_log WHERE created_at < ?", (audit_cutoff,))
    counts["admin_audit_expired"] = max(0, cursor.rowcount)

    # The gift feature only displays recent gifts; retain at most five per
    # recipient and never retain a gift message for more than 30 days.
    cursor = db.execute(
        """DELETE FROM gifts
           WHERE id NOT IN (
               SELECT id FROM gifts AS recent
               WHERE recent.target_uid = gifts.target_uid
               ORDER BY recent.id DESC
               LIMIT ?
           )""",
        (GIFT_MAX_PER_RECIPIENT,),
    )
    counts["excess_gifts_deleted"] = max(0, cursor.rowcount)

    cursor = db.execute("DELETE FROM translation_daily_usage WHERE day_key < ?", (today_key,))
    counts["translation_usage_expired"] = max(0, cursor.rowcount)

    return counts
