"""Regression and transaction tests for Katheryne's economy service."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
import pytest

from katherine_economy import (
    EconomyAccountMissing, EconomyConflict, EconomyInsufficientFunds,
    EconomyInvalidAmount, EconomyService, seed_default_store,
)


@pytest.fixture
def economy(tmp_path):
    path = tmp_path / "economy.sqlite3"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE users(
            uid TEXT PRIMARY KEY, nickname TEXT NOT NULL, points INTEGER NOT NULL DEFAULT 0,
            last_seen REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE economy_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL, operation TEXT NOT NULL, reason TEXT NOT NULL,
            reference TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX uq_economy_idem ON economy_ledger(uid,idempotency_key)
            WHERE idempotency_key<>'';
        CREATE TABLE economy_store_items(
            item_key TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,
            price INTEGER NOT NULL,category TEXT NOT NULL,stock INTEGER,
            max_per_user INTEGER NOT NULL,enabled INTEGER NOT NULL,
            created_at REAL NOT NULL,updated_at REAL NOT NULL
        );
        CREATE TABLE economy_inventory(
            uid TEXT NOT NULL,item_key TEXT NOT NULL,quantity INTEGER NOT NULL,
            updated_at REAL NOT NULL,PRIMARY KEY(uid,item_key)
        );
    """)
    db.executemany("INSERT INTO users(uid,nickname,points,last_seen) VALUES(?,?,?,?)", [
        ("community:room:alice", "Alice", 250, 1),
        ("community:room:bob", "Bob", 60, 1),
        ("community:elsewhere:carol", "Carol", 90, 1),
    ])
    db.commit()
    db.close()

    @contextmanager
    def session():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    clock = [1000.0]
    return EconomyService(db_session=session, clock=lambda: clock[0]), path, clock


def test_wallet_credit_and_debit_are_recorded_atomically(economy):
    service, _, _ = economy
    credited = service.credit("community:room:alice", 25, reason="daily")
    assert credited.balance == 275
    debited = service.debit("community:room:alice", 15, reason="purchase")
    assert debited.balance == 260
    assert service.reconcile_balance("community:room:alice")
    history = service.history("community:room:alice")
    assert [entry.delta for entry in history[:2]] == [-15, 25]


def test_debit_rejects_negative_balance_and_invalid_amounts(economy):
    service, _, _ = economy
    with pytest.raises(EconomyInsufficientFunds):
        service.debit("community:room:bob", 61)
    with pytest.raises(EconomyInvalidAmount):
        service.credit("community:room:bob", 0)
    with pytest.raises(EconomyInvalidAmount):
        service.credit("community:room:bob", -1)
    assert service.balance("community:room:bob") == 60


def test_idempotency_prevents_duplicate_rewards(economy):
    service, _, _ = economy
    service.credit("community:room:alice", 50, idempotency_key="daily:2026-11-18")
    service.credit("community:room:alice", 50, idempotency_key="daily:2026-11-18")
    assert service.balance("community:room:alice") == 300
    assert len(service.history("community:room:alice")) == 1


def test_transfer_is_atomic_and_room_scoped(economy):
    service, _, _ = economy
    result = service.transfer(
        "community:room:alice", "community:room:bob", 100,
        idempotency_key="gift-001",
    )
    assert (result.sender_balance, result.recipient_balance) == (150, 160)
    replay = service.transfer(
        "community:room:alice", "community:room:bob", 100,
        idempotency_key="gift-001",
    )
    assert (replay.sender_balance, replay.recipient_balance) == (150, 160)
    with pytest.raises(EconomyConflict):
        service.transfer("community:room:alice", "community:elsewhere:carol", 1)
    with pytest.raises(EconomyInsufficientFunds):
        service.transfer("community:room:alice", "community:room:bob", 151)


def test_store_purchase_enforces_stock_and_inventory_limits(economy):
    service, _, _ = economy
    service.define_item("ticket", "Ticket", "Expedition", 40, stock=2, max_per_user=2)
    purchased = service.buy("community:room:alice", "ticket", 2)
    assert purchased.total_cost == 80
    assert purchased.remaining_balance == 170
    assert purchased.inventory_quantity == 2
    with pytest.raises(EconomyConflict):
        service.buy("community:room:alice", "ticket", 1)
    with pytest.raises(EconomyInsufficientFunds):
        service.buy("community:room:bob", "ticket", 2)


def test_inventory_consume_and_grant(economy):
    service, _, _ = economy
    service.define_item("flower", "Flower", "Gift", 5, max_per_user=20)
    assert service.grant_item("community:room:alice", "flower", 3) == 3
    assert service.consume_item("community:room:alice", "flower", 2) == 1
    assert service.consume_item("community:room:alice", "flower", 1) == 0
    with pytest.raises(EconomyConflict):
        service.consume_item("community:room:alice", "flower", 1)


def test_leaderboard_can_be_filtered_by_community_scope(economy):
    service, _, _ = economy
    top = service.leaderboard(scope_prefix="community:room:", limit=5)
    assert [item.uid for item in top] == ["community:room:alice", "community:room:bob"]


def test_default_store_catalog_is_repeatable(economy):
    service, _, _ = economy
    assert seed_default_store(service) == 12
    assert seed_default_store(service) == 12
    assert len(service.list_items()) == 12


def test_unknown_account_and_disabled_item_are_rejected(economy):
    service, _, _ = economy
    with pytest.raises(EconomyAccountMissing):
        service.credit("missing", 1)
    service.define_item("disabled", "Disabled", "", 1, enabled=False)
    with pytest.raises(EconomyConflict):
        service.buy("community:room:alice", "disabled", 1)
