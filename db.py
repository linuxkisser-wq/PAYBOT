"""Асинхронный слой доступа к PostgreSQL с пулом соединений."""

import logging
from typing import Any, Optional

import asyncpg

from config import DATABASE_URL, TICKET_COUNTER_START

log = logging.getLogger(__name__)

# --- Состояния FSM пользователя -------------------------------------------------
STATE_IDLE = "IDLE"
STATE_CHOOSE_REASON = "CHOOSE_REASON"
STATE_DESCRIBE = "DESCRIBE"
STATE_ATTACH_PHOTO = "ATTACH_PHOTO"
STATE_CONFIRM = "CONFIRM"
STATE_ACTIVE = "ACTIVE"
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
    id           INTEGER PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    username     TEXT,
    reason       TEXT NOT NULL,
    description  TEXT NOT NULL,
    photo_id     TEXT,
    status       TEXT DEFAULT 'pending',
    admin_msg_id BIGINT,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    closed_at    TIMESTAMPTZ,
    accepted_at  TIMESTAMPTZ,
    rating       INTEGER,
    sla_pinged   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id      BIGINT PRIMARY KEY,
    state        TEXT DEFAULT 'IDLE',
    ticket_id    INTEGER,
    temp_reason  TEXT,
    temp_desc    TEXT,
    temp_photo   TEXT
);

CREATE TABLE IF NOT EXISTS ticket_counter (
    id           SMALLINT PRIMARY KEY CHECK (id = 1),
    last_id      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_state (
    chat_id           BIGINT PRIMARY KEY,
    current_ticket_id INTEGER,
    reject_ticket_id  INTEGER
);

CREATE TABLE IF NOT EXISTS admin_msg_map (
    admin_msg_id BIGINT PRIMARY KEY,
    ticket_id    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_user_status ON tickets(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
"""

_pool: Optional[asyncpg.Pool] = None


async def init() -> None:
    """Создать пул соединений и подготовить схему."""
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("Переменная DATABASE_URL не задана в .env.")

    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        server_settings={"timezone": "UTC"},
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA)
            await _migrate(conn)
            await conn.execute(
                """INSERT INTO ticket_counter (id, last_id) VALUES (1, $1)
                   ON CONFLICT (id) DO NOTHING""",
                TICKET_COUNTER_START,
            )
    except BaseException:
        await pool.close()
        raise
    _pool = pool
    log.info("Подключено к PostgreSQL")


async def _migrate(conn: asyncpg.Connection) -> None:
    """Добавить колонки, отсутствующие в ранних PostgreSQL-схемах."""
    additions = {
        "accepted_at": "TIMESTAMPTZ",
        "rating": "INTEGER",
        "sla_pinged": "INTEGER DEFAULT 0",
    }
    for column, definition in additions.items():
        await conn.execute(
            f"ALTER TABLE tickets ADD COLUMN IF NOT EXISTS {column} {definition}"
        )


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _db() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db.init() не был вызван")
    return _pool


# --- Нумерация тикетов ----------------------------------------------------------


async def next_ticket_id() -> int:
    """Атомарно выдать следующий номер. Первый вызов вернёт 1."""
    async with _db().acquire() as conn:
        ticket_id = await conn.fetchval(
            """UPDATE ticket_counter SET last_id = last_id + 1
               WHERE id = 1 RETURNING last_id"""
        )
    if ticket_id is None:
        raise RuntimeError("Счётчик тикетов не инициализирован")
    return int(ticket_id)


# --- Сессии ---------------------------------------------------------------------


async def get_session(user_id: int) -> dict[str, Any]:
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO sessions (user_id, state) VALUES ($1, $2)
               ON CONFLICT (user_id) DO NOTHING""",
            user_id,
            STATE_IDLE,
        )
        row = await conn.fetchrow("SELECT * FROM sessions WHERE user_id = $1", user_id)
    return dict(row)


async def update_session(user_id: int, **fields: Any) -> None:
    """Обновить поля сессии. Допустимые ключи — колонки sessions."""
    allowed = {"state", "ticket_id", "temp_reason", "temp_desc", "temp_photo"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Неизвестные поля сессии: {unknown}")
    if not fields:
        return

    assignments = ", ".join(f"{key} = ${index}" for index, key in enumerate(fields, 2))
    values = [user_id, *fields.values()]
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO sessions (user_id, state) VALUES ($1, $2)
               ON CONFLICT (user_id) DO NOTHING""",
            user_id,
            STATE_IDLE,
        )
        await conn.execute(
            f"UPDATE sessions SET {assignments} WHERE user_id = $1", *values
        )


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
    async with _db().acquire() as conn:
        return await conn.fetchval(
            "SELECT user_id FROM sessions WHERE ticket_id = $1 AND state = $2",
            ticket_id,
            STATE_ACTIVE,
        )


# --- Тикеты ---------------------------------------------------------------------


async def create_ticket(
    ticket_id: int,
    user_id: int,
    username: Optional[str],
    reason: str,
    description: str,
    photo_id: Optional[str],
) -> None:
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO tickets (id, user_id, username, reason, description, photo_id, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            ticket_id,
            user_id,
            username,
            reason,
            description,
            photo_id,
            STATUS_PENDING,
        )


async def get_ticket(ticket_id: int) -> Optional[dict[str, Any]]:
    async with _db().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
    return dict(row) if row else None


async def set_admin_msg_id(ticket_id: int, admin_msg_id: int) -> None:
    async with _db().acquire() as conn:
        await conn.execute(
            "UPDATE tickets SET admin_msg_id = $1 WHERE id = $2", admin_msg_id, ticket_id
        )


async def set_status(ticket_id: int, status: str, closed: bool = False) -> None:
    async with _db().acquire() as conn:
        if closed:
            await conn.execute(
                """UPDATE tickets SET status = $1, closed_at = CURRENT_TIMESTAMP
                   WHERE id = $2""",
                status,
                ticket_id,
            )
        else:
            await conn.execute("UPDATE tickets SET status = $1 WHERE id = $2", status, ticket_id)


async def get_open_ticket_for_user(user_id: int) -> Optional[dict[str, Any]]:
    """Открытая заявка пользователя: pending или active."""
    async with _db().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM tickets
               WHERE user_id = $1 AND status IN ($2, $3)
               ORDER BY id DESC LIMIT 1""",
            user_id,
            STATUS_PENDING,
            STATUS_ACTIVE,
        )
    return dict(row) if row else None


async def get_last_ticket_for_user(user_id: int) -> Optional[dict[str, Any]]:
    async with _db().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tickets WHERE user_id = $1 ORDER BY id DESC LIMIT 1", user_id
        )
    return dict(row) if row else None


async def get_expired_pending(hours: int) -> list[dict[str, Any]]:
    """Заявки в статусе pending старше N часов."""
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM tickets
               WHERE status = $1
                 AND created_at <= CURRENT_TIMESTAMP - ($2::double precision * INTERVAL '1 hour')""",
            STATUS_PENDING,
            hours,
        )
    return [dict(row) for row in rows]


async def accept_ticket(ticket_id: int) -> None:
    """Принять заявку и зафиксировать время реакции."""
    async with _db().acquire() as conn:
        await conn.execute(
            """UPDATE tickets SET status = $1, accepted_at = CURRENT_TIMESTAMP
               WHERE id = $2""",
            STATUS_ACTIVE,
            ticket_id,
        )


async def set_rating(ticket_id: int, rating: int) -> None:
    async with _db().acquire() as conn:
        await conn.execute("UPDATE tickets SET rating = $1 WHERE id = $2", rating, ticket_id)


async def get_sla_overdue(minutes: int) -> list[dict[str, Any]]:
    """Непринятые заявки старше N минут, о которых админу ещё не напоминали."""
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM tickets
               WHERE status = $1 AND sla_pinged = 0
                 AND created_at <= CURRENT_TIMESTAMP - ($2::double precision * INTERVAL '1 minute')""",
            STATUS_PENDING,
            minutes,
        )
    return [dict(row) for row in rows]


async def mark_sla_pinged(ticket_id: int) -> None:
    async with _db().acquire() as conn:
        await conn.execute("UPDATE tickets SET sla_pinged = 1 WHERE id = $1", ticket_id)


async def get_user_history(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tickets WHERE user_id = $1 ORDER BY id DESC LIMIT $2",
            user_id,
            limit,
        )
    return [dict(row) for row in rows]


async def get_stats() -> dict[str, Any]:
    """Сводка для админа: количество по статусам, скорость реакции, оценки."""
    async with _db().acquire() as conn:
        status_rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM tickets GROUP BY status"
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        avg_accept = await conn.fetchval(
            """SELECT AVG(EXTRACT(EPOCH FROM (accepted_at - created_at)) / 60.0)
               FROM tickets WHERE accepted_at IS NOT NULL"""
        )
        ratings = await conn.fetchrow(
            """SELECT AVG(rating) AS avg_rating, COUNT(rating) AS n
               FROM tickets WHERE rating IS NOT NULL"""
        )
        reasons = await conn.fetch(
            """SELECT reason, COUNT(*) AS n FROM tickets
               GROUP BY reason ORDER BY n DESC LIMIT 3"""
        )

    return {
        "total": total,
        "by_status": {row["status"]: row["n"] for row in status_rows},
        "avg_accept_minutes": avg_accept,
        "avg_rating": ratings["avg_rating"],
        "ratings_count": ratings["n"],
        "top_reasons": [(row["reason"], row["n"]) for row in reasons],
    }


async def get_active_tickets() -> list[dict[str, Any]]:
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tickets WHERE status = $1 ORDER BY id", STATUS_ACTIVE
        )
    return [dict(row) for row in rows]


async def get_open_tickets() -> list[dict[str, Any]]:
    """Всё, что требует внимания админа: и ждущие, и уже в работе."""
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM tickets WHERE status IN ($1, $2)
               ORDER BY (status = $3) DESC, id""",
            STATUS_PENDING,
            STATUS_ACTIVE,
            STATUS_PENDING,
        )
    return [dict(row) for row in rows]


async def get_undelivered_pending() -> list[dict[str, Any]]:
    """Заявки, чья карточка не дошла до админа."""
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM tickets
               WHERE status = $1 AND admin_msg_id IS NULL ORDER BY id""",
            STATUS_PENDING,
        )
    return [dict(row) for row in rows]


# --- Состояние администратора ---------------------------------------------------


async def get_admin_state(chat_id: int) -> dict[str, Any]:
    async with _db().acquire() as conn:
        await conn.execute(
            "INSERT INTO admin_state (chat_id) VALUES ($1) ON CONFLICT (chat_id) DO NOTHING",
            chat_id,
        )
        row = await conn.fetchrow("SELECT * FROM admin_state WHERE chat_id = $1", chat_id)
    return dict(row)


async def update_admin_state(chat_id: int, **fields: Any) -> None:
    allowed = {"current_ticket_id", "reject_ticket_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Неизвестные поля admin_state: {unknown}")
    if not fields:
        return

    assignments = ", ".join(f"{key} = ${index}" for index, key in enumerate(fields, 2))
    values = [chat_id, *fields.values()]
    async with _db().acquire() as conn:
        await conn.execute(
            "INSERT INTO admin_state (chat_id) VALUES ($1) ON CONFLICT (chat_id) DO NOTHING",
            chat_id,
        )
        await conn.execute(
            f"UPDATE admin_state SET {assignments} WHERE chat_id = $1", *values
        )


# --- Карта сообщений админа -> тикет --------------------------------------------


async def map_admin_msg(admin_msg_id: int, ticket_id: int) -> None:
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO admin_msg_map (admin_msg_id, ticket_id) VALUES ($1, $2)
               ON CONFLICT (admin_msg_id) DO UPDATE SET ticket_id = EXCLUDED.ticket_id""",
            admin_msg_id,
            ticket_id,
        )


async def ticket_by_admin_msg(admin_msg_id: int) -> Optional[int]:
    async with _db().acquire() as conn:
        return await conn.fetchval(
            "SELECT ticket_id FROM admin_msg_map WHERE admin_msg_id = $1", admin_msg_id
        )
