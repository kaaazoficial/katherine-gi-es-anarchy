"""Transactional community economy for Katheryne.

The service uses the existing scoped users.points balance as the wallet source
of truth and records every mutation in an append-only ledger. It never stores
chat content, only functional transaction metadata.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


_ITEM_KEY = re.compile(r"[a-z0-9][a-z0-9_.-]{1,47}\Z")
_REASON_LIMIT = 120
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


class EconomyError(Exception):
    """Base exception for rejected economy operations."""


class EconomyAccountMissing(EconomyError):
    """Raised when an operation references an unregistered profile."""


class EconomyInsufficientFunds(EconomyError):
    """Raised when a debit would create a negative balance."""


class EconomyInvalidAmount(EconomyError):
    """Raised when a quantity or amount is outside the supported bounds."""


class EconomyConflict(EconomyError):
    """Raised when a stock limit, duplicate key or idempotency conflict occurs."""


@dataclass(frozen=True, slots=True)
class Wallet:
    uid: str
    balance: int
    nickname: str


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: int
    uid: str
    delta: int
    balance_after: int
    operation: str
    reason: str
    reference: str
    created_at: float


@dataclass(frozen=True, slots=True)
class StoreItem:
    key: str
    name: str
    description: str
    price: int
    category: str
    stock: int | None
    max_per_user: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    item: StoreItem
    quantity: int
    total_cost: int
    remaining_balance: int
    inventory_quantity: int


@dataclass(frozen=True, slots=True)
class TransferResult:
    amount: int
    sender_balance: int
    recipient_balance: int
    reference: str


def _positive_int(value: Any, *, maximum: int = 1_000_000) -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EconomyInvalidAmount("La cantidad debe ser un entero.") from exc
    if amount <= 0 or amount > maximum:
        raise EconomyInvalidAmount("La cantidad está fuera del límite permitido.")
    return amount


def _safe_text(value: Any, limit: int, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] if text else fallback


def _scope(uid: str) -> str:
    """Return the room scope embedded in the repository's scoped UID format."""
    parts = str(uid).split(":")
    return ":".join(parts[:-1]) if len(parts) > 1 else ""


class EconomyService:
    """Atomic wallet, transfer, store and inventory operations."""

    def __init__(
        self,
        *,
        db_session: Callable[[], Any],
        clock: Callable[[], float] = time.time,
        max_transfer: int = 10_000,
        max_purchase_quantity: int = 99,
    ) -> None:
        if max_transfer < 1 or max_purchase_quantity < 1:
            raise ValueError("Economy limits must be positive.")
        self.db_session = db_session
        self.clock = clock
        self.max_transfer = int(max_transfer)
        self.max_purchase_quantity = int(max_purchase_quantity)

    def wallet(self, uid: str) -> Wallet | None:
        with self.db_session() as db:
            row = db.execute(
                "SELECT uid,nickname,points FROM users WHERE uid=?",
                (str(uid),),
            ).fetchone()
        if row is None:
            return None
        return Wallet(str(row["uid"]), int(row["points"]), str(row["nickname"]))

    def balance(self, uid: str) -> int:
        account = self.wallet(uid)
        return account.balance if account else 0

    def _existing_idempotent(self, db: Any, uid: str, key: str) -> LedgerEntry | None:
        if not key:
            return None
        row = db.execute(
            """SELECT id,uid,delta,balance_after,operation,reason,reference,created_at
               FROM economy_ledger WHERE uid=? AND idempotency_key=?""",
            (uid, key),
        ).fetchone()
        return self._ledger_from_row(row) if row else None

    @staticmethod
    def _ledger_from_row(row: Any) -> LedgerEntry:
        return LedgerEntry(
            id=int(row["id"]),
            uid=str(row["uid"]),
            delta=int(row["delta"]),
            balance_after=int(row["balance_after"]),
            operation=str(row["operation"]),
            reason=str(row["reason"]),
            reference=str(row["reference"] or ""),
            created_at=float(row["created_at"]),
        )

    def _insert_ledger(
        self,
        db: Any,
        *,
        uid: str,
        delta: int,
        balance_after: int,
        operation: str,
        reason: str,
        reference: str = "",
        idempotency_key: str = "",
    ) -> int:
        cursor = db.execute(
            """INSERT INTO economy_ledger(
                   uid,delta,balance_after,operation,reason,reference,
                   idempotency_key,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                str(uid), int(delta), int(balance_after),
                _safe_text(operation, 32, "adjustment"),
                _safe_text(reason, _REASON_LIMIT, "Operación comunitaria"),
                str(reference)[:96], str(idempotency_key)[:120],
                float(self.clock()),
            ),
        )
        return int(cursor.lastrowid)

    def apply(
        self,
        uid: str,
        delta: int,
        *,
        operation: str = "adjustment",
        reason: str = "Operación comunitaria",
        reference: str = "",
        idempotency_key: str = "",
        allow_negative: bool = False,
    ) -> Wallet:
        """Apply a signed balance change and append its ledger row atomically."""
        amount = int(delta)
        if amount == 0 or abs(amount) > 10_000_000:
            raise EconomyInvalidAmount("El movimiento debe ser distinto de cero y estar acotado.")
        key = str(idempotency_key or "").strip()[:120]
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._existing_idempotent(db, str(uid), key) if key else None
            if previous:
                account = db.execute(
                    "SELECT uid,nickname,points FROM users WHERE uid=?", (str(uid),)
                ).fetchone()
                if account is None:
                    raise EconomyAccountMissing("El perfil no está registrado.")
                return Wallet(str(uid), int(account["points"]), str(account["nickname"]))
            row = db.execute(
                "SELECT uid,nickname,points FROM users WHERE uid=?",
                (str(uid),),
            ).fetchone()
            if row is None:
                raise EconomyAccountMissing("El perfil no está registrado.")
            current = int(row["points"])
            target = current + amount
            if target < 0 and not allow_negative:
                raise EconomyInsufficientFunds("No hay saldo suficiente.")
            db.execute(
                "UPDATE users SET points=? WHERE uid=?",
                (target, str(uid)),
            )
            self._insert_ledger(
                db, uid=str(uid), delta=amount, balance_after=target,
                operation=operation, reason=reason, reference=reference,
                idempotency_key=key,
            )
            return Wallet(str(uid), target, str(row["nickname"]))

    def credit(
        self, uid: str, amount: int, *, reason: str = "Recompensa",
        reference: str = "", idempotency_key: str = "",
    ) -> Wallet:
        return self.apply(
            uid, _positive_int(amount, maximum=10_000_000),
            operation="credit", reason=reason, reference=reference,
            idempotency_key=idempotency_key,
        )

    def debit(
        self, uid: str, amount: int, *, reason: str = "Compra",
        reference: str = "", idempotency_key: str = "",
    ) -> Wallet:
        return self.apply(
            uid, -_positive_int(amount, maximum=10_000_000),
            operation="debit", reason=reason, reference=reference,
            idempotency_key=idempotency_key,
        )

    def transfer(
        self,
        sender_uid: str,
        recipient_uid: str,
        amount: int,
        *,
        reason: str = "Transferencia entre viajeros",
        idempotency_key: str = "",
        enforce_same_scope: bool = True,
    ) -> TransferResult:
        value = _positive_int(amount, maximum=self.max_transfer)
        sender, recipient = str(sender_uid), str(recipient_uid)
        if not sender or not recipient or sender == recipient:
            raise EconomyInvalidAmount("La transferencia no es válida.")
        if enforce_same_scope and _scope(sender) != _scope(recipient):
            raise EconomyConflict("La transferencia debe permanecer dentro de la misma sala.")
        key = str(idempotency_key or "").strip()[:120]
        reference = key or hashlib.sha256(
            f"{sender}:{recipient}:{value}:{self.clock()}".encode()
        ).hexdigest()[:24]
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._existing_idempotent(db, sender, f"{reference}:debit")
            prior_credit = self._existing_idempotent(db, recipient, f"{reference}:credit")
            if prior and prior_credit:
                rows = db.execute(
                    "SELECT uid,points FROM users WHERE uid IN (?,?)",
                    (sender, recipient),
                ).fetchall()
                balances = {str(row["uid"]): int(row["points"]) for row in rows}
                return TransferResult(value, balances.get(sender, 0), balances.get(recipient, 0), reference)
            source_row = db.execute(
                "SELECT points FROM users WHERE uid=?", (sender,)
            ).fetchone()
            target_row = db.execute(
                "SELECT points FROM users WHERE uid=?", (recipient,)
            ).fetchone()
            if source_row is None or target_row is None:
                raise EconomyAccountMissing("Ambos perfiles deben estar registrados.")
            source_balance = int(source_row["points"])
            target_balance = int(target_row["points"])
            if source_balance < value:
                raise EconomyInsufficientFunds("No hay saldo suficiente.")
            source_after = source_balance - value
            target_after = target_balance + value
            db.execute("UPDATE users SET points=? WHERE uid=?", (source_after, sender))
            db.execute("UPDATE users SET points=? WHERE uid=?", (target_after, recipient))
            self._insert_ledger(
                db, uid=sender, delta=-value, balance_after=source_after,
                operation="transfer_out", reason=reason,
                reference=reference, idempotency_key=f"{reference}:debit" if key else "",
            )
            self._insert_ledger(
                db, uid=recipient, delta=value, balance_after=target_after,
                operation="transfer_in", reason=reason,
                reference=reference, idempotency_key=f"{reference}:credit" if key else "",
            )
        return TransferResult(value, source_after, target_after, reference)

    def define_item(
        self,
        key: str,
        name: str,
        description: str,
        price: int,
        *,
        category: str = "general",
        stock: int | None = None,
        max_per_user: int = 99,
        enabled: bool = True,
    ) -> StoreItem:
        item_key = str(key or "").strip().casefold()
        if not _ITEM_KEY.fullmatch(item_key):
            raise ValueError("La clave del artículo no es válida.")
        cost = int(price)
        if cost < 0 or cost > 10_000_000:
            raise EconomyInvalidAmount("El precio debe estar entre 0 y 10.000.000.")
        stock_value = None if stock is None else _positive_int(stock, maximum=10_000_000)
        maximum = _positive_int(max_per_user, maximum=999)
        now = float(self.clock())
        with self.db_session() as db:
            db.execute(
                """INSERT INTO economy_store_items(
                       item_key,name,description,price,category,stock,
                       max_per_user,enabled,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(item_key) DO UPDATE SET
                       name=excluded.name,description=excluded.description,
                       price=excluded.price,category=excluded.category,
                       stock=excluded.stock,max_per_user=excluded.max_per_user,
                       enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (
                    item_key, _safe_text(name, 80, item_key),
                    _safe_text(description, 240), cost,
                    _safe_text(category, 32, "general"),
                    stock_value, maximum, 1 if enabled else 0, now, now,
                ),
            )
        return self.item(item_key)  # type: ignore[return-value]

    @staticmethod
    def _item_from_row(row: Any) -> StoreItem:
        return StoreItem(
            key=str(row["item_key"]), name=str(row["name"]),
            description=str(row["description"]), price=int(row["price"]),
            category=str(row["category"]),
            stock=None if row["stock"] is None else int(row["stock"]),
            max_per_user=int(row["max_per_user"]), enabled=bool(row["enabled"]),
        )

    def item(self, key: str) -> StoreItem | None:
        with self.db_session() as db:
            row = db.execute(
                "SELECT * FROM economy_store_items WHERE item_key=?",
                (str(key).casefold(),),
            ).fetchone()
        return self._item_from_row(row) if row else None

    def list_items(
        self, *, category: str | None = None, include_disabled: bool = False,
    ) -> list[StoreItem]:
        sql = "SELECT * FROM economy_store_items WHERE 1=1"
        params: list[Any] = []
        if not include_disabled:
            sql += " AND enabled=1"
        if category:
            sql += " AND category=?"
            params.append(str(category))
        sql += " ORDER BY category,name,item_key"
        with self.db_session() as db:
            rows = db.execute(sql, tuple(params)).fetchall()
        return [self._item_from_row(row) for row in rows]

    def inventory(self, uid: str) -> list[dict[str, Any]]:
        with self.db_session() as db:
            rows = db.execute(
                """SELECT i.item_key,i.quantity,i.updated_at,s.name,s.description,
                          s.category,s.price
                   FROM economy_inventory AS i
                   LEFT JOIN economy_store_items AS s ON s.item_key=i.item_key
                   WHERE i.uid=? AND i.quantity>0
                   ORDER BY COALESCE(s.category,'retired'),COALESCE(s.name,i.item_key)""",
                (str(uid),),
            ).fetchall()
        return [dict(row) for row in rows]

    def buy(self, uid: str, item_key: str, quantity: int = 1) -> PurchaseResult:
        amount = _positive_int(quantity, maximum=self.max_purchase_quantity)
        key = str(item_key or "").strip().casefold()
        now = float(self.clock())
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            item_row = db.execute(
                "SELECT * FROM economy_store_items WHERE item_key=? AND enabled=1",
                (key,),
            ).fetchone()
            if item_row is None:
                raise EconomyConflict("El artículo no está disponible.")
            item = self._item_from_row(item_row)
            if amount > item.max_per_user:
                raise EconomyInvalidAmount("La cantidad supera el límite por viajero.")
            stock = item.stock
            if stock is not None and stock < amount:
                raise EconomyConflict("No hay existencias suficientes.")
            account = db.execute(
                "SELECT points FROM users WHERE uid=?", (str(uid),)
            ).fetchone()
            if account is None:
                raise EconomyAccountMissing("El perfil no está registrado.")
            current = int(account["points"])
            total = item.price * amount
            if current < total:
                raise EconomyInsufficientFunds("No hay saldo suficiente.")
            owned_row = db.execute(
                "SELECT quantity FROM economy_inventory WHERE uid=? AND item_key=?",
                (str(uid), key),
            ).fetchone()
            owned = int(owned_row["quantity"]) if owned_row else 0
            if owned + amount > item.max_per_user:
                raise EconomyConflict("El inventario alcanzaría el límite del artículo.")
            remaining = current - total
            db.execute("UPDATE users SET points=? WHERE uid=?", (remaining, str(uid)))
            db.execute(
                """INSERT INTO economy_inventory(uid,item_key,quantity,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(uid,item_key) DO UPDATE SET
                   quantity=economy_inventory.quantity+excluded.quantity,
                   updated_at=excluded.updated_at""",
                (str(uid), key, amount, now),
            )
            if stock is not None:
                cursor = db.execute(
                    """UPDATE economy_store_items SET stock=stock-?,updated_at=?
                       WHERE item_key=? AND stock>=?""",
                    (amount, now, key, amount),
                )
                if cursor.rowcount != 1:
                    raise EconomyConflict("El stock cambió durante la compra.")
            self._insert_ledger(
                db, uid=str(uid), delta=-total, balance_after=remaining,
                operation="purchase", reason=f"Compra: {item.name}",
                reference=key,
            )
            current_owned = owned + amount
        return PurchaseResult(item, amount, total, remaining, current_owned)

    def consume_item(self, uid: str, item_key: str, quantity: int = 1) -> int:
        amount = _positive_int(quantity, maximum=self.max_purchase_quantity)
        key = str(item_key or "").strip().casefold()
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT quantity FROM economy_inventory WHERE uid=? AND item_key=?",
                (str(uid), key),
            ).fetchone()
            if row is None or int(row["quantity"]) < amount:
                raise EconomyConflict("No tienes suficientes unidades de ese artículo.")
            remaining = int(row["quantity"]) - amount
            if remaining:
                db.execute(
                    "UPDATE economy_inventory SET quantity=?,updated_at=? WHERE uid=? AND item_key=?",
                    (remaining, float(self.clock()), str(uid), key),
                )
            else:
                db.execute(
                    "DELETE FROM economy_inventory WHERE uid=? AND item_key=?",
                    (str(uid), key),
                )
        return remaining

    def grant_item(self, uid: str, item_key: str, quantity: int = 1) -> int:
        amount = _positive_int(quantity, maximum=10_000)
        key = str(item_key or "").strip().casefold()
        if not self.item(key):
            raise EconomyConflict("El artículo no existe en el catálogo.")
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO economy_inventory(uid,item_key,quantity,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(uid,item_key) DO UPDATE SET
                   quantity=economy_inventory.quantity+excluded.quantity,
                   updated_at=excluded.updated_at""",
                (str(uid), key, amount, float(self.clock())),
            )
            row = db.execute(
                "SELECT quantity FROM economy_inventory WHERE uid=? AND item_key=?",
                (str(uid), key),
            ).fetchone()
        return int(row["quantity"])

    def leaderboard(self, *, scope_prefix: str = "", limit: int = 10) -> list[Wallet]:
        count = max(1, min(_MAX_PAGE_SIZE, int(limit)))
        sql = "SELECT uid,nickname,points FROM users"
        params: list[Any] = []
        if scope_prefix:
            sql += " WHERE substr(uid,1,?)=?"
            params.extend([len(scope_prefix), scope_prefix])
        sql += " ORDER BY points DESC,last_seen DESC LIMIT ?"
        params.append(count)
        with self.db_session() as db:
            rows = db.execute(sql, tuple(params)).fetchall()
        return [
            Wallet(str(row["uid"]), int(row["points"]), str(row["nickname"]))
            for row in rows
        ]

    def history(self, uid: str, *, limit: int = _DEFAULT_PAGE_SIZE) -> list[LedgerEntry]:
        count = max(1, min(_MAX_PAGE_SIZE, int(limit)))
        with self.db_session() as db:
            rows = db.execute(
                """SELECT id,uid,delta,balance_after,operation,reason,reference,created_at
                   FROM economy_ledger WHERE uid=? ORDER BY id DESC LIMIT ?""",
                (str(uid), count),
            ).fetchall()
        return [self._ledger_from_row(row) for row in rows]

    def reconcile_balance(self, uid: str) -> bool:
        """Check ledger continuity without exposing any other account."""
        entries = self.history(uid, limit=_MAX_PAGE_SIZE)
        if not entries:
            return True
        chronological = list(reversed(entries))
        previous = None
        for entry in chronological:
            if previous is not None and previous + entry.delta != entry.balance_after:
                return False
            previous = entry.balance_after
        account = self.wallet(uid)
        return account is not None and account.balance == chronological[-1].balance_after

    def purge_user_economy(self, uid: str) -> dict[str, int]:
        """Remove a user's optional game inventory and ledger during account deletion."""
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            inventory = db.execute(
                "DELETE FROM economy_inventory WHERE uid=?", (str(uid),)
            ).rowcount
            ledger = db.execute(
                "DELETE FROM economy_ledger WHERE uid=?", (str(uid),)
            ).rowcount
        return {"inventory_rows": int(inventory), "ledger_rows": int(ledger)}


def seed_default_store(service: EconomyService) -> int:
    """Install a small, transparent catalogue; existing customized items are updated."""
    catalogue = (
        ("namecard_mondstadt", "Tarjeta de Mondstadt", "Marco de perfil temático de Mondstadt.", 180, "cosmetic", None, 1),
        ("namecard_liyue", "Tarjeta de Liyue", "Marco de perfil temático de Liyue.", 180, "cosmetic", None, 1),
        ("namecard_inazuma", "Tarjeta de Inazuma", "Marco de perfil temático de Inazuma.", 180, "cosmetic", None, 1),
        ("namecard_sumeru", "Tarjeta de Sumeru", "Marco de perfil temático de Sumeru.", 180, "cosmetic", None, 1),
        ("namecard_fontaine", "Tarjeta de Fontaine", "Marco de perfil temático de Fontaine.", 180, "cosmetic", None, 1),
        ("namecard_natlan", "Tarjeta de Natlan", "Marco de perfil temático de Natlan.", 180, "cosmetic", None, 1),
        ("expedition_ticket", "Pase de expedición", "Objeto coleccionable para actividades comunitarias.", 75, "utility", 500, 5),
        ("resin_snack", "Tentempié de resina", "Objeto funcional reservado para futuras dinámicas del bot.", 60, "utility", 500, 10),
        ("lucky_charm", "Amuleto de la suerte", "Coleccionable de buena fortuna.", 120, "collectible", 250, 10),
        ("primogem_badge", "Insignia de protogema", "Insignia decorativa de colección, no representa moneda real.", 300, "collectible", 100, 3),
        ("friendship_flower", "Flor de amistad", "Regalo simbólico para la comunidad.", 45, "social", None, 20),
        ("celebration_cake", "Pastel de celebración", "Objeto de temporada para eventos comunitarios.", 90, "seasonal", 100, 5),
    )
    count = 0
    for key, name, desc, price, category, stock, maximum in catalogue:
        service.define_item(
            key, name, desc, price, category=category, stock=stock,
            max_per_user=maximum, enabled=True,
        )
        count += 1
    return count
