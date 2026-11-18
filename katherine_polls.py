"""Persistent, room-scoped community polls with bounded retention."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any


class PollError(Exception):
    """Base error for poll operations."""


class PollNotFound(PollError):
    """The poll does not exist in the requested room."""


class PollClosed(PollError):
    """The poll is no longer accepting votes."""


class PollInvalid(PollError):
    """Poll input is malformed or outside policy limits."""


class PollPermissionError(PollError):
    """The actor cannot perform the requested poll action."""


_CODE_RE = re.compile(r"[0-9]{6}\Z")
_BUTTON_RE = re.compile(r"[0-9]{6,20}\Z")
_MIN_DURATION = 60
_MAX_DURATION = 604800
_RETENTION_GRACE = 86400


class PollRepository:
    """SQLite-backed poll lifecycle, voting and expiry management.

    Public votes are scoped to a poll and room. Anonymous polls store only a
    one-way voter digest, never the raw platform UID. All records are deleted
    after the poll's maximum lifetime plus a one-day cleanup grace period.
    """

    def __init__(
        self,
        *,
        db_session: Callable[[], Any],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_session = db_session
        self.clock = clock

    @staticmethod
    def _normalize_options(options: Any) -> list[str]:
        if not isinstance(options, (list, tuple)):
            raise PollInvalid("Las opciones deben ser una lista.")
        clean = [" ".join(str(value or "").split())[:40] for value in options]
        clean = [value for value in clean if value]
        if not 2 <= len(clean) <= 5:
            raise PollInvalid("Una encuesta necesita entre dos y cinco opciones.")
        if len({value.casefold() for value in clean}) != len(clean):
            raise PollInvalid("Las opciones no pueden estar duplicadas.")
        return clean

    @staticmethod
    def _from_row(row: Any, votes: dict[str, int] | None = None) -> dict[str, Any]:
        return {
            "id": str(row["poll_id"]),
            "code": str(row["code"]),
            "source": str(row["source"]),
            "thread": str(row["thread_id"]),
            "question": str(row["question"]),
            "options": json.loads(row["options_json"]),
            "votes": dict(votes or {}),
            "creator_uid": str(row["creator_uid"]),
            "creator_name": str(row["creator_name"]),
            "anonymous": bool(row["anonymous"]),
            "closed": bool(row["closed"]),
            "created_at": float(row["created_at"]),
            "closes_at": float(row["closes_at"]),
            "expires": float(row["expires_at"]),
            "button_key": str(row["button_key"]),
        }

    def create(
        self,
        *,
        poll_id: str,
        code: str,
        source: str,
        thread_id: str,
        question: str,
        options: list[str],
        creator_uid: str,
        creator_name: str,
        anonymous: bool,
        duration_seconds: int,
        button_key: str,
    ) -> dict[str, Any]:
        now = float(self.clock())
        duration = int(duration_seconds)
        clean_options = self._normalize_options(options)
        clean_question = " ".join(str(question or "").split())[:180]
        if not clean_question:
            raise PollInvalid("La pregunta no puede estar vacía.")
        if not _CODE_RE.fullmatch(str(code)):
            raise PollInvalid("El código de encuesta no es válido.")
        if not _BUTTON_RE.fullmatch(str(button_key)):
            raise PollInvalid("La clave de interacción no es válida.")
        if not 1 <= len(str(thread_id)) <= 128 or not source:
            raise PollInvalid("La sala o el origen no son válidos.")
        if not 1 <= len(str(creator_uid)) <= 256:
            raise PollInvalid("No se pudo identificar al creador.")
        if not _MIN_DURATION <= duration <= _MAX_DURATION:
            raise PollInvalid("La duración debe estar entre un minuto y siete días.")
        closes_at = now + duration
        with self.db_session() as db:
            db.execute(
                """INSERT INTO community_polls(
                    poll_id,code,source,thread_id,question,options_json,
                    creator_uid,creator_name,anonymous,closed,created_at,
                    closes_at,expires_at,button_key
                ) VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                (
                    str(poll_id), str(code), str(source), str(thread_id),
                    clean_question,
                    json.dumps(clean_options, ensure_ascii=False, separators=(",", ":")),
                    str(creator_uid), str(creator_name)[:80], 1 if anonymous else 0,
                    now, closes_at, closes_at + _RETENTION_GRACE, str(button_key),
                ),
            )
            row = db.execute(
                "SELECT * FROM community_polls WHERE poll_id=?",
                (str(poll_id),),
            ).fetchone()
        return self._from_row(row)

    def _get(self, where: str, value: str, *, source: str | None = None,
             thread_id: str | None = None) -> dict[str, Any] | None:
        if where not in {"poll_id", "button_key", "code"}:
            raise ValueError("Unsupported poll lookup.")
        sql = f"SELECT * FROM community_polls WHERE {where}=?"
        params: list[Any] = [str(value)]
        if source is not None:
            sql += " AND source=?"
            params.append(str(source))
        if thread_id is not None:
            sql += " AND thread_id=?"
            params.append(str(thread_id))
        with self.db_session() as db:
            row = db.execute(sql, tuple(params)).fetchone()
            if row is None:
                return None
            votes = db.execute(
                "SELECT voter_key,option_index FROM community_poll_votes WHERE poll_id=?",
                (str(row["poll_id"]),),
            ).fetchall()
        return self._from_row(
            row, {str(vote["voter_key"]): int(vote["option_index"]) for vote in votes}
        )

    def get(self, poll_id: str, *, source: str | None = None,
            thread_id: str | None = None) -> dict[str, Any] | None:
        return self._get("poll_id", poll_id, source=source, thread_id=thread_id)

    def get_by_button_key(self, button_key: str, *, source: str | None = None,
                          thread_id: str | None = None) -> dict[str, Any] | None:
        return self._get("button_key", button_key, source=source, thread_id=thread_id)

    def get_by_code(self, code: str, *, source: str, thread_id: str) -> dict[str, Any] | None:
        return self._get("code", code, source=source, thread_id=thread_id)

    @staticmethod
    def _voter_key(poll: dict[str, Any], uid: str) -> str:
        if poll["anonymous"]:
            payload = f"{poll['id']}:{poll['code']}:{uid}".encode("utf-8")
            return hashlib.sha256(payload).hexdigest()
        return str(uid)

    def vote(
        self, poll_id: str, uid: str, option_index: int, *,
        source: str, thread_id: str,
    ) -> dict[str, Any]:
        voter_uid = str(uid or "").strip()
        if not voter_uid:
            raise PollInvalid("No se pudo identificar a quien vota.")
        now = float(self.clock())
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM community_polls
                   WHERE poll_id=? AND source=? AND thread_id=?""",
                (str(poll_id), str(source), str(thread_id)),
            ).fetchone()
            if row is None:
                raise PollNotFound("No encontré esa encuesta en esta sala.")
            poll = self._from_row(row)
            if bool(row["closed"]) or float(row["closes_at"]) <= now:
                db.execute(
                    "UPDATE community_polls SET closed=1 WHERE poll_id=?",
                    (str(poll_id),),
                )
                raise PollClosed("La encuesta ya está cerrada.")
            if not 0 <= int(option_index) < len(poll["options"]):
                raise PollInvalid("La opción seleccionada no existe.")
            voter_key = self._voter_key(poll, voter_uid)
            db.execute(
                """INSERT INTO community_poll_votes(
                    poll_id,voter_key,option_index,created_at
                ) VALUES(?,?,?,?)
                ON CONFLICT(poll_id,voter_key) DO UPDATE SET
                    option_index=excluded.option_index,
                    created_at=excluded.created_at""",
                (str(poll_id), voter_key, int(option_index), now),
            )
        updated = self.get(poll_id, source=source, thread_id=thread_id)
        if updated is None:
            raise PollNotFound("La encuesta dejó de estar disponible.")
        return updated

    def close(
        self, poll_id: str, actor_uid: str, *, source: str, thread_id: str,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT creator_uid FROM community_polls
                   WHERE poll_id=? AND source=? AND thread_id=?""",
                (str(poll_id), str(source), str(thread_id)),
            ).fetchone()
            if row is None:
                raise PollNotFound("No encontré esa encuesta en esta sala.")
            if str(row["creator_uid"]) != str(actor_uid) and not is_admin:
                raise PollPermissionError("Solo quien creó la encuesta o un administrador puede cerrarla.")
            db.execute(
                "UPDATE community_polls SET closed=1 WHERE poll_id=?",
                (str(poll_id),),
            )
        result = self.get(poll_id, source=source, thread_id=thread_id)
        if result is None:
            raise PollNotFound("La encuesta dejó de estar disponible.")
        return result

    def close_expired(self, *, now: float | None = None) -> int:
        current = float(self.clock() if now is None else now)
        with self.db_session() as db:
            cursor = db.execute(
                """UPDATE community_polls SET closed=1
                   WHERE closed=0 AND closes_at<=?""",
                (current,),
            )
        return int(cursor.rowcount)

    def purge_expired(self, *, now: float | None = None) -> dict[str, int]:
        current = float(self.clock() if now is None else now)
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            poll_ids = [
                str(row["poll_id"])
                for row in db.execute(
                    "SELECT poll_id FROM community_polls WHERE expires_at<=?",
                    (current,),
                ).fetchall()
            ]
            votes = 0
            for poll_id in poll_ids:
                votes += db.execute(
                    "DELETE FROM community_poll_votes WHERE poll_id=?",
                    (poll_id,),
                ).rowcount
            polls = db.execute(
                "DELETE FROM community_polls WHERE expires_at<=?",
                (current,),
            ).rowcount
        return {"polls": int(polls), "votes": int(votes)}
