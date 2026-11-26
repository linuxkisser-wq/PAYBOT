"""Операции над тикетами, общие для пользовательского и админского роутеров."""

import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

import db
import keyboards
import texts
from config import ADMIN_CHAT_ID, TICKET_TIMEOUT_HOURS

log = logging.getLogger(__name__)


async def deliver_ticket_to_admin(bot: Bot, ticket: dict[str, Any]) -> None:
    """Отправить карточку заявки администратору. Фото — отдельным медиа-сообщением."""
    card = await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=texts.admin_new_ticket(
            ticket_id=ticket["id"],
            username=ticket["username"],
            user_id=ticket["user_id"],
            reason=ticket["reason"],
            description=ticket["description"],
            has_photo=bool(ticket["photo_id"]),
        ),
        reply_markup=keyboards.admin_ticket_menu(ticket["id"]),
    )
    await db.set_admin_msg_id(ticket["id"], card.message_id)
    await db.map_admin_msg(card.message_id, ticket["id"])

    if ticket["photo_id"]:
        photo_msg = await bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=ticket["photo_id"],
            caption=f"🖼 Скриншот к заявке {texts.tn(ticket['id'])}",
        )
        await db.map_admin_msg(photo_msg.message_id, ticket["id"])

    log.info("Заявка %s доставлена админу (msg_id=%s)", texts.tn(ticket["id"]), card.message_id)


async def close_ticket(bot: Bot, ticket_id: int) -> bool:
    """Закрыть активный тикет: уведомить обе стороны, снять сессию.

    Возвращает False, если тикет не найден или уже не активен.
    """
    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["status"] != db.STATUS_ACTIVE:
        return False

    await db.set_status(ticket_id, db.STATUS_CLOSED, closed=True)
    await db.reset_session(ticket["user_id"])

    admin_state = await db.get_admin_state(ADMIN_CHAT_ID)
    if admin_state["current_ticket_id"] == ticket_id:
        await db.update_admin_state(ADMIN_CHAT_ID, current_ticket_id=None)

    await safe_send(bot, ticket["user_id"], texts.ticket_closed_user(ticket_id))
    # Отдельным сообщением, чтобы кнопки оценки не мешали тексту о закрытии.
    try:
        await bot.send_message(
            chat_id=ticket["user_id"],
            text=texts.ASK_RATING,
            reply_markup=keyboards.rating_menu(ticket_id),
        )
    except TelegramAPIError as exc:
        log.warning("Запрос оценки по %s не доставлен: %s", texts.tn(ticket_id), exc)

    log.info("Тикет %s закрыт", texts.tn(ticket_id))
    return True


async def ping_sla(bot: Bot, ticket: dict[str, Any], minutes: int) -> None:
    """Напомнить админу о заявке, которая висит без ответа."""
    ticket_id = ticket["id"]
    await db.mark_sla_pinged(ticket_id)

    text = texts.admin_sla_ping(ticket_id, minutes, ticket["username"])
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            reply_to_message_id=ticket["admin_msg_id"],
        )
    except TelegramAPIError:
        # Карточку могли удалить — тогда шлём напоминание без привязки.
        await safe_send(bot, ADMIN_CHAT_ID, text)

    log.info("SLA-напоминание по заявке %s (%s мин)", texts.tn(ticket_id), minutes)


async def expire_ticket(bot: Bot, ticket: dict[str, Any]) -> None:
    """Просрочить нерассмотренную заявку."""
    ticket_id = ticket["id"]
    await db.set_status(ticket_id, db.STATUS_EXPIRED, closed=True)
    await db.reset_session(ticket["user_id"])

    if ticket["admin_msg_id"]:
        try:
            await bot.edit_message_text(
                chat_id=ADMIN_CHAT_ID,
                message_id=ticket["admin_msg_id"],
                text=texts.admin_ticket_expired(ticket_id, TICKET_TIMEOUT_HOURS),
                reply_markup=None,
            )
        except TelegramAPIError as exc:
            log.warning("Не удалось отредактировать карточку %s: %s", texts.tn(ticket_id), exc)

    await safe_send(
        bot, ticket["user_id"], texts.ticket_expired_user(ticket_id, TICKET_TIMEOUT_HOURS)
    )
    log.info("Заявка %s истекла по таймауту", texts.tn(ticket_id))


async def safe_send(bot: Bot, chat_id: int, text: str) -> bool:
    """Отправить сообщение, не роняя обработчик, если пользователь заблокировал бота."""
    try:
        await bot.send_message(chat_id=chat_id, text=text)
        return True
    except TelegramAPIError as exc:
        log.warning("Не доставлено в чат %s: %s", chat_id, exc)
        return False


async def safe_send_photo(
    bot: Bot, chat_id: int, photo_id: str, caption: Optional[str] = None
) -> bool:
    try:
        await bot.send_photo(chat_id=chat_id, photo=photo_id, caption=caption)
        return True
    except TelegramAPIError as exc:
        log.warning("Фото не доставлено в чат %s: %s", chat_id, exc)
        return False
