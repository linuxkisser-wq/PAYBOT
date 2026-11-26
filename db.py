"""Слой доступа к SQLite (aiosqlite).

Одно соединение на всё приложение + asyncio.Lock вокруг операций, которым нужна
атомарность (выдача номера тикета).
"""

import asyncio
import logging
from typing import Any, Optional

import aiosqlite

from config import DB_PATH, TICKET_COUNTER_START

log = logging.getLogger(__name__)

# --- Состояния FSM пользователя -------------------------------------------------
STATE_IDLE = "IDLE"
STATE_CHOOSE_REASON = "CHOOSE_REASON"
STATE_DESCRIBE = "DESCRIBE"
STATE_ATTACH_PHOTO = "ATTACH_PHOTO"
STATE_CONFIRM = "CONFIRM"
STATE_ACTIVE = "ACTIVE"
# Точечная правка полей с шага подтверждения: возвращают сразу к карточке.
STATE_EDIT_REASON = "EDIT_REASON"
STATE_EDIT_DESC = "EDIT_DESC"
STATE_EDIT_PHOTO = "EDIT_PHOTO"

# --- Статусы тикета -------------------------------------------------------------
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    reason       TEXT NOT NULL,
    description  TEXT NOT NULL,
    photo_id     TEXT,
    status       TEXT DEFAULT 'pending',
    admin_msg_id INTEGER,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at    TIMESTAMP,
    accepted_at  TIMESTAMP,   -- когда админ принял: считаем скорость реакции
    rating       INTEGER,     -- оценка пользователя после закрытия, 1..5
    sla_pinged   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id      INTEGER PRIMARY KEY,
    state        TEXT DEFAULT 'IDLE',
    ticket_id    INTEGER,
    temp_reason  TEXT,
    temp_desc    TEXT,
    temp_photo   TEXT
);

CREATE TABLE IF NOT EXISTS ticket_counter (
    last_id      INTEGER DEFAULT 1000
);

-- Состояние администратора: текущий диалог и ожидание причины отказа.
CREATE TABLE IF NOT EXISTS admin_state (
    chat_id           INTEGER PRIMARY KEY,
    current_ticket_id INTEGER,
    reject_ticket_id  INTEGER
);

-- Карта "сообщение в чате админа -> тикет", чтобы reply роутился в нужный тикет.
CREATE TABLE IF NOT EXISTS admin_msg_map (
    admin_msg_id INTEGER PRIMARY KEY,
    ticket_id    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_user_status ON tickets(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
"""

_conn: Optional[aiosqlite.Connection] = None
_counter_lock = asyncio.Lock()


async def init() -> None:
    """Открыть соединение и создать схему."""
    global _conn
    # isolation_level=None — автокоммит, чтобы явный BEGIN IMMEDIATE в
    # next_ticket_id() не конфликтовал с неявными транзакциями sqlite3.
    _conn = await aiosqlite.connect(DB_PATH, isolation_level=None)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA foreign_keys=ON")
    await _conn.executescript(SCHEMA)
    await _migrate()

    cur = await _conn.execute("SELECT COUNT(*) AS n FROM ticket_counter")
    row = await cur.fetchone()
    if row["n"] == 0:
        await _conn.execute(
            "INSERT INTO ticket_counter (last_id) VALUES (?)", (TICKET_COUNTER_START,)
        )
    await _conn.commit()
    log.info("БД готова: %s", DB_PATH)


async def _migrate() -> None:
    """Добить колонки, которых нет в базах, созданных ранней версией схемы."""
    cur = await _db().execute("PRAGMA table_info(tickets)")
    existing = {row["name"] for row in await cur.fetchall()}

    additions = {
        "accepted_at": "ALTER TABLE tickets ADD COLUMN accepted_at TIMESTAMP",
        "rating": "ALTER TABLE tickets ADD COLUMN rating INTEGER",
        "sla_pinged": "ALTER TABLE tickets ADD COLUMN sla_pinged INTEGER DEFAULT 0",
    }
    for column, statement in additions.items():
        if column not in existing:
            await _db().execute(statement)
            log.info("Миграция: добавлена колонка tickets.%s", column)


async def close() -> None:
    if _conn is not None:
        await _conn.close()


def _db() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("db.init() не был вызван")
    return _conn


# --- Нумерация тикетов ----------------------------------------------------------


async def next_ticket_id() -> int:
    """Атомарно выдать следующий номер. Первый вызов вернёт 1001."""
    async with _counter_lock:
        db = _db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute("UPDATE ticket_counter SET last_id = last_id + 1")
            cur = await db.execute("SELECT last_id FROM ticket_counter LIMIT 1")
            row = await cur.fetchone()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return int(row["last_id"])


# --- Сессии ---------------------------------------------------------------------


async def get_session(user_id: int) -> dict[str, Any]:
    db = _db()
    cur = await db.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    if row is None:
        await db.execute(
            "INSERT INTO sessions (user_id, state) VALUES (?, ?)", (user_id, STATE_IDLE)
        )
        await db.commit()
        return {
            "user_id": user_id,
            "state": STATE_IDLE,
            "ticket_id": None,
            "temp_reason": None,
            "temp_desc": None,
            "temp_photo": None,
        }
    return dict(row)


async def update_session(user_id: int, **fields: Any) -> None:
    """Обновить поля сессии. Допустимые ключи — колонки sessions."""
    allowed = {"state", "ticket_id", "temp_reason", "temp_desc", "temp_photo"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Неизвестные поля сессии: {unknown}")

    await get_session(user_id)  # гарантируем, что строка есть
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [user_id]
    db = _db()
    await db.execute(f"UPDATE sessions SET {assignments} WHERE user_id = ?", values)
    await db.commit()


async def reset_session(user_id: int) -> None:
    """Сбросить в IDLE и очистить черновик."""
    await update_session(
        user_id,
        state=STATE_IDLE,
        ticket_id=None,
        temp_reason=None,
        temp_desc=None,
        temp_photo=None,
    )


async def get_active_session_user(ticket_id: int) -> Optional[int]:
    db = _db()
    cur = await db.execute(
        "SELECT user_id FROM sessions WHERE ticket_id = ? AND state = ?",
        (ticket_id, STATE_ACTIVE),
    )
    row = await cur.fetchone()
    return row["user_id"] if row else None


# --- Тикеты ---------------------------------------------------------------------


async def create_ticket(
    ticket_id: int,
    user_id: int,
    username: Optional[str],
    reason: str,
    description: str,
    photo_id: Optional[str],
) -> None:
    db = _db()
    await db.execute(
        """INSERT INTO tickets (id, user_id, username, reason, description, photo_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ticket_id, user_id, username, reason, description, photo_id, STATUS_PENDING),
    )
    await db.commit()


async def get_ticket(ticket_id: int) -> Optional[dict[str, Any]]:
    db = _db()
    cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def set_admin_msg_id(ticket_id: int, admin_msg_id: int) -> None:
    db = _db()
    await db.execute(
        "UPDATE tickets SET admin_msg_id = ? WHERE id = ?", (admin_msg_id, ticket_id)
    )
    await db.commit()


async def set_status(ticket_id: int, status: str, closed: bool = False) -> None:
    db = _db()
    if closed:
        await db.execute(
            "UPDATE tickets SET status = ?, closed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, ticket_id),
        )
    else:
        await db.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
    await db.commit()


async def get_open_ticket_for_user(user_id: int) -> Optional[dict[str, Any]]:
    """Открытая заявка пользователя: pending или active."""
    db = _db()
    cur = await db.execute(
        """SELECT * FROM tickets
           WHERE user_id = ? AND status IN (?, ?)
           ORDER BY id DESC LIMIT 1""",
        (user_id, STATUS_PENDING, STATUS_ACTIVE),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_last_ticket_for_user(user_id: int) -> Optional[dict[str, Any]]:
    db = _db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_expired_pending(hours: int) -> list[dict[str, Any]]:
    """Заявки в статусе pending старше N часов."""
    db = _db()
    cur = await db.execute(
        f"""SELECT * FROM tickets
            WHERE status = ? AND created_at <= datetime('now', '-{int(hours)} hours')""",
        (STATUS_PENDING,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def accept_ticket(ticket_id: int) -> None:
    """Принять заявку и зафиксировать время реакции."""
    db = _db()
    await db.execute(
        "UPDATE tickets SET status = ?, accepted_at = CURRENT_TIMESTAMP WHERE id = ?",
        (STATUS_ACTIVE, ticket_id),
    )
    await db.commit()


async def set_rating(ticket_id: int, rating: int) -> None:
    db = _db()
    await db.execute("UPDATE tickets SET rating = ? WHERE id = ?", (rating, ticket_id))
    await db.commit()


async def get_sla_overdue(minutes: int) -> list[dict[str, Any]]:
    """Непринятые заявки старше N минут, о которых админу ещё не напоминали."""
    db = _db()
    cur = await db.execute(
        f"""SELECT * FROM tickets
            WHERE status = ? AND sla_pinged = 0
              AND created_at <= datetime('now', '-{int(minutes)} minutes')""",
        (STATUS_PENDING,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def mark_sla_pinged(ticket_id: int) -> None:
    db = _db()
    await db.execute("UPDATE tickets SET sla_pinged = 1 WHERE id = ?", (ticket_id,))
    await db.commit()


async def get_user_history(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    db = _db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_stats() -> dict[str, Any]:
    """Сводка для админа: количество по статусам, скорость реакции, оценки."""
    db = _db()

    cur = await db.execute("SELECT status, COUNT(*) AS n FROM tickets GROUP BY status")
    by_status = {row["status"]: row["n"] for row in await cur.fetchall()}

    cur = await db.execute("SELECT COUNT(*) AS n FROM tickets")
    total = (await cur.fetchone())["n"]

    # Среднее время от создания до принятия, в минутах.
    cur = await db.execute(
        """SELECT AVG((julianday(accepted_at) - julianday(created_at)) * 1440.0) AS avg_min
           FROM tickets WHERE accepted_at IS NOT NULL"""
    )
    avg_accept = (await cur.fetchone())["avg_min"]

    cur = await db.execute(
        "SELECT AVG(rating) AS avg_rating, COUNT(rating) AS n FROM tickets WHERE rating IS NOT NULL"
    )
    row = await cur.fetchone()

    cur = await db.execute(
        """SELECT reason, COUNT(*) AS n FROM tickets
           GROUP BY reason ORDER BY n DESC LIMIT 3"""
    )
    top_reasons = [(r["reason"], r["n"]) for r in await cur.fetchall()]

    return {
        "total": total,
        "by_status": by_status,
        "avg_accept_minutes": avg_accept,
        "avg_rating": row["avg_rating"],
        "ratings_count": row["n"],
        "top_reasons": top_reasons,
    }


async def get_active_tickets() -> list[dict[str, Any]]:
    db = _db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE status = ? ORDER BY id", (STATUS_ACTIVE,)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_open_tickets() -> list[dict[str, Any]]:
    """Всё, что требует внимания админа: и ждущие, и уже в работе."""
    db = _db()
    cur = await db.execute(
        """SELECT * FROM tickets WHERE status IN (?, ?)
           ORDER BY (status = ?) DESC, id""",
        (STATUS_PENDING, STATUS_ACTIVE, STATUS_PENDING),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_undelivered_pending() -> list[dict[str, Any]]:
    """Заявки, чья карточка не дошла до админа (сбой сети, падение хендлера).

    Без этого заявка молча висит до истечения таймаута: пользователю сказали
    «отправлено», а админ её не видит.
    """
    db = _db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE status = ? AND admin_msg_id IS NULL ORDER BY id",
        (STATUS_PENDING,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# --- Состояние администратора ---------------------------------------------------


async def get_admin_state(chat_id: int) -> dict[str, Any]:
    db = _db()
    cur = await db.execute("SELECT * FROM admin_state WHERE chat_id = ?", (chat_id,))
    row = await cur.fetchone()
    if row is None:
        await db.execute("INSERT INTO admin_state (chat_id) VALUES (?)", (chat_id,))
        await db.commit()
        return {"chat_id": chat_id, "current_ticket_id": None, "reject_ticket_id": None}
    return dict(row)


async def update_admin_state(chat_id: int, **fields: Any) -> None:
    allowed = {"current_ticket_id", "reject_ticket_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Неизвестные поля admin_state: {unknown}")

    await get_admin_state(chat_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [chat_id]
    db = _db()
    await db.execute(f"UPDATE admin_state SET {assignments} WHERE chat_id = ?", values)
    await db.commit()


# --- Карта сообщений админа -> тикет --------------------------------------------


async def map_admin_msg(admin_msg_id: int, ticket_id: int) -> None:
    db = _db()
    await db.execute(
        "INSERT OR REPLACE INTO admin_msg_map (admin_msg_id, ticket_id) VALUES (?, ?)",
        (admin_msg_id, ticket_id),
    )
    await db.commit()


async def ticket_by_admin_msg(admin_msg_id: int) -> Optional[int]:
    db = _db()
    cur = await db.execute(
        "SELECT ticket_id FROM admin_msg_map WHERE admin_msg_id = ?", (admin_msg_id,)
    )
    row = await cur.fetchone()
    return row["ticket_id"] if row else None
