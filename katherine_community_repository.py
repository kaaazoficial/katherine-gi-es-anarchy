"""SQLite repository for community profiles and room-scoped identity lookups."""
from __future__ import annotations
import time
from collections.abc import Callable
from typing import Any


class CommunityRepository:
    """Own profile and activity persistence while keeping SQL out of webhook code."""
    def __init__(self, *, db_session: Callable[[], Any],
                 activity_clock_bucket: Callable[[], tuple[str, int]],
                 clock: Callable[[], float] = time.time) -> None:
        self.db_session = db_session
        self.activity_clock_bucket = activity_clock_bucket
        self.clock = clock

    def record_user_and_chat_activity(self, uid: str, nickname: str, thread_id: str,
                                      *, count_message: bool = True) -> None:
        now = self.clock()
        day_key, hour = self.activity_clock_bucket()
        safe_uid = str(uid)
        safe_nickname = str(nickname or "Viajero")[:80]
        with self.db_session() as db:
            db.execute(
                """INSERT INTO users(uid,nickname,created_at,last_seen,message_count)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(uid) DO UPDATE SET nickname=excluded.nickname,
                   last_seen=excluded.last_seen,
                   message_count=users.message_count + excluded.message_count""",
                (safe_uid, safe_nickname, now, now, 1 if count_message else 0),
            )
            if thread_id:
                db.execute(
                    """INSERT INTO chat_activity_daily(
                           thread_id,uid,nickname,day_key,message_count
                       ) VALUES(?,?,?,?,1)
                       ON CONFLICT(thread_id,uid,day_key) DO UPDATE SET
                       nickname=excluded.nickname,
                       message_count=chat_activity_daily.message_count+1""",
                    (str(thread_id), safe_uid, safe_nickname, day_key),
                )
                db.execute(
                    """INSERT INTO chat_activity_hourly(
                           thread_id,day_key,hour_of_day,message_count
                       ) VALUES(?,?,?,1)
                       ON CONFLICT(thread_id,day_key,hour_of_day) DO UPDATE SET
                       message_count=chat_activity_hourly.message_count+1""",
                    (str(thread_id), day_key, hour),
                )

    def record_user(self, uid: str, nickname: str, count_message: bool = False) -> None:
        now = self.clock()
        with self.db_session() as db:
            db.execute(
                """INSERT INTO users(uid,nickname,created_at,last_seen,message_count)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(uid) DO UPDATE SET nickname=excluded.nickname,
                   last_seen=excluded.last_seen,
                   message_count=users.message_count + excluded.message_count""",
                (str(uid), str(nickname or "Viajero")[:80], now, now,
                 1 if count_message else 0),
            )

    def get_user(self, uid: str) -> dict[str, Any] | None:
        with self.db_session() as db:
            row = db.execute("SELECT * FROM users WHERE uid=?", (str(uid),)).fetchone()
        return dict(row) if row else None

    def get_user_profile(self, uid: str, viewer_uid: str) -> tuple[dict | None, int]:
        """Read profile and viewer-relative affinity in one query."""
        with self.db_session() as db:
            row = db.execute(
                """SELECT u.*, COALESCE(a.score,0) AS viewer_affinity
                   FROM users AS u
                   LEFT JOIN affinities AS a ON a.uid=? AND a.target_uid=u.uid
                   WHERE u.uid=?""",
                (str(viewer_uid), str(uid)),
            ).fetchone()
        if row is None:
            return None, 0
        profile = dict(row)
        affinity = int(profile.pop("viewer_affinity", 0) or 0)
        return profile, affinity

    def find_user_profile_by_nickname(self, nickname: str, scope_key: str | None,
                                      viewer_uid: str) -> tuple[dict | None, int]:
        params: list[Any] = [str(viewer_uid), str(nickname).lstrip("@")]
        sql = (
            "SELECT u.*, COALESCE(a.score,0) AS viewer_affinity "
            "FROM users AS u LEFT JOIN affinities AS a "
            "ON a.uid=? AND a.target_uid=u.uid "
            "WHERE lower(u.nickname)=lower(?)"
        )
        if scope_key:
            prefix = str(scope_key) + ":"
            sql += " AND substr(u.uid,1,?)=?"
            params.extend([len(prefix), prefix])
        sql += " ORDER BY u.last_seen DESC LIMIT 1"
        with self.db_session() as db:
            row = db.execute(sql, tuple(params)).fetchone()
        if row is None:
            return None, 0
        profile = dict(row)
        affinity = int(profile.pop("viewer_affinity", 0) or 0)
        return profile, affinity

    def find_user_by_nickname(self, nickname: str, scope_key: str | None = None) -> dict[str, Any] | None:
        with self.db_session() as db:
            sql = "SELECT * FROM users WHERE lower(nickname)=lower(?)"
            params: list[Any] = [str(nickname).lstrip("@")]
            if scope_key:
                prefix = str(scope_key) + ":"
                sql += " AND substr(uid,1,?)=?"
                params.extend([len(prefix), prefix])
            sql += " ORDER BY last_seen DESC LIMIT 1"
            row = db.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None
