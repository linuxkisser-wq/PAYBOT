"""Админская часть: приём/отклонение заявок, анонимные ответы, закрытие тикетов.

Роутер целиком отфильтрован по ADMIN_CHAT_ID, поэтому дополнительных проверок
прав внутри хендлеров не требуется.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji

import db
import keyboards
import services
import texts
from config import ADMIN_CHAT_ID

log = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.chat.id == ADMIN_CHAT_ID)
router.callback_query.filter(F.message.chat.id == ADMIN_CHAT_ID)


# --- Кнопки на карточке заявки --------------------------------------------------


@router.callback_query(F.data.startswith("admin:accept:"))
async def cb_accept(callback: CallbackQuery, bot: Bot) -> None:
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    ticket = await db.get_ticket(ticket_id)

    if ticket is None:
        await callback.answer(texts.admin_ticket_not_found(ticket_id), show_alert=True)
        return
    if ticket["status"] != db.STATUS_PENDING:
        await callback.answer(
            texts.admin_ticket_not_active(ticket_id, ticket["status"]), show_alert=True
        )
        return

    await db.accept_ticket(ticket_id)
    await db.update_session(ticket["user_id"], state=db.STATE_ACTIVE, ticket_id=ticket_id)
    await db.update_admin_state(ADMIN_CHAT_ID, current_ticket_id=ticket_id)

    await callback.message.edit_text(texts.admin_ticket_accepted(ticket_id), reply_markup=None)
    await callback.answer()

    delivered = await services.safe_send(
        bot, ticket["user_id"], texts.ticket_accepted_user(ticket_id)
    )
    if not delivered:
        await callback.message.answer(texts.ADMIN_DELIVERY_FAILED)

    log.info("Заявка %s принята, чат открыт с user_id=%s", texts.tn(ticket_id), ticket["user_id"])


@router.callback_query(F.data.startswith("admin:card:"))
async def cb_show_card(callback: CallbackQuery, bot: Bot) -> None:
    """Переотправить карточку заявки — если исходная не дошла или удалена."""
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    ticket = await db.get_ticket(ticket_id)

    if ticket is None:
        await callback.answer(texts.admin_ticket_not_found(ticket_id), show_alert=True)
        return
    if ticket["status"] != db.STATUS_PENDING:
        await callback.answer(
            texts.admin_ticket_not_active(ticket_id, ticket["status"]), show_alert=True
        )
        return

    await services.deliver_ticket_to_admin(bot, ticket)
    await callback.answer("Карточка отправлена ниже")


@router.callback_query(F.data.startswith("admin:reject:"))
async def cb_reject(callback: CallbackQuery) -> None:
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    ticket = await db.get_ticket(ticket_id)

    if ticket is None:
        await callback.answer(texts.admin_ticket_not_found(ticket_id), show_alert=True)
        return
    if ticket["status"] != db.STATUS_PENDING:
        await callback.answer(
            texts.admin_ticket_not_active(ticket_id, ticket["status"]), show_alert=True
        )
        return

    # Карточка удаляется, причина запрашивается текстом.
    try:
        await callback.message.delete()
    except TelegramAPIError as exc:
        log.warning("Карточку %s не удалось удалить: %s", texts.tn(ticket_id), exc)

    await db.update_admin_state(ADMIN_CHAT_ID, reject_ticket_id=ticket_id)
    await callback.message.answer(texts.admin_ask_reject_reason(ticket_id))
    await callback.answer()


# --- Команды --------------------------------------------------------------------


@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.ADMIN_HELP)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    state = await db.get_admin_state(ADMIN_CHAT_ID)
    if state["reject_ticket_id"]:
        await db.update_admin_state(ADMIN_CHAT_ID, reject_ticket_id=None)
        await message.answer("Отклонение отменено. Заявка осталась в статусе «на рассмотрении».")
        return
    await message.answer(texts.ADMIN_HELP)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await message.answer(texts.admin_stats(await db.get_stats()))


@router.message(Command("tickets"))
async def cmd_tickets(message: Message) -> None:
    """Открытые тикеты: и ждущие принятия, и активные."""
    tickets = await db.get_open_tickets()
    if not tickets:
        await message.answer(texts.ADMIN_NO_TICKETS)
        return
    state = await db.get_admin_state(ADMIN_CHAT_ID)
    await message.answer(
        texts.admin_tickets_list(tickets, state["current_ticket_id"]),
        reply_markup=keyboards.pending_tickets_menu(tickets),
    )


@router.message(Command("reply"))
async def cmd_reply(message: Message, command: CommandObject) -> None:
    """Сделать тикет текущим диалогом: /reply 1001."""
    if not command.args or not command.args.strip().lstrip("#").isdigit():
        await message.answer("Формат: /reply 1001")
        return

    ticket_id = int(command.args.strip().lstrip("#"))
    ticket = await db.get_ticket(ticket_id)
    if ticket is None:
        await message.answer(texts.admin_ticket_not_found(ticket_id))
        return
    if ticket["status"] != db.STATUS_ACTIVE:
        await message.answer(texts.admin_ticket_not_active(ticket_id, ticket["status"]))
        return

    await db.update_admin_state(ADMIN_CHAT_ID, current_ticket_id=ticket_id)
    await message.answer(texts.admin_current_ticket_set(ticket_id))


@router.message(Command("stop"))
async def cmd_stop(message: Message, command: CommandObject, bot: Bot) -> None:
    """Закрыть тикет: /stop (текущий) или /stop 1001."""
    state = await db.get_admin_state(ADMIN_CHAT_ID)

    if command.args and command.args.strip().lstrip("#").isdigit():
        ticket_id = int(command.args.strip().lstrip("#"))
    else:
        ticket_id = state["current_ticket_id"]

    if ticket_id is None:
        await message.answer(texts.ADMIN_STOP_USAGE)
        return

    ticket = await db.get_ticket(ticket_id)
    if ticket is None:
        await message.answer(texts.admin_ticket_not_found(ticket_id))
        return
    if ticket["status"] != db.STATUS_ACTIVE:
        await message.answer(texts.admin_ticket_not_active(ticket_id, ticket["status"]))
        return

    await services.close_ticket(bot, ticket_id)
    await message.answer(texts.admin_ticket_closed(ticket_id))


# --- Сообщения админа -----------------------------------------------------------


async def _resolve_target_ticket(message: Message) -> int | None:
    """Ответ (reply) на пересланное сообщение → его тикет, иначе текущий диалог."""
    if message.reply_to_message:
        ticket_id = await db.ticket_by_admin_msg(message.reply_to_message.message_id)
        if ticket_id is not None:
            return ticket_id

    state = await db.get_admin_state(ADMIN_CHAT_ID)
    return state["current_ticket_id"]


@router.message(F.text | F.photo)
async def on_admin_message(message: Message, bot: Bot) -> None:
    state = await db.get_admin_state(ADMIN_CHAT_ID)

    # 1. Ждём причину отклонения — она имеет приоритет.
    if state["reject_ticket_id"] and message.text:
        await _apply_rejection(message, bot, state["reject_ticket_id"])
        return

    # 2. Обычный ответ в активный тикет — уходит от имени «Поддержки Nexora Pay».
    ticket_id = await _resolve_target_ticket(message)
    if ticket_id is None:
        await message.answer(texts.ADMIN_NO_ACTIVE)
        return

    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["status"] != db.STATUS_ACTIVE:
        await db.update_admin_state(ADMIN_CHAT_ID, current_ticket_id=None)
        await message.answer(texts.ADMIN_NO_ACTIVE)
        return

    if message.photo:
        ok = await services.safe_send_photo(
            bot, ticket["user_id"], message.photo[-1].file_id, caption=message.caption
        )
    else:
        ok = await services.safe_send(bot, ticket["user_id"], message.text)

    if not ok:
        await message.answer(texts.ADMIN_DELIVERY_FAILED)
        return

    # Отмечаем доставку, не засоряя чат: реакция вместо сообщения.
    try:
        await message.react([ReactionTypeEmoji(emoji="👍")])
    except TelegramAPIError:
        pass

    log.info("Ответ админа доставлен по тикету %s", texts.tn(ticket_id))


async def _apply_rejection(message: Message, bot: Bot, ticket_id: int) -> None:
    reason = message.text.strip()
    ticket = await db.get_ticket(ticket_id)

    await db.update_admin_state(ADMIN_CHAT_ID, reject_ticket_id=None)

    if ticket is None:
        await message.answer(texts.admin_ticket_not_found(ticket_id))
        return
    if ticket["status"] != db.STATUS_PENDING:
        await message.answer(texts.admin_ticket_not_active(ticket_id, ticket["status"]))
        return

    await db.set_status(ticket_id, db.STATUS_REJECTED, closed=True)
    await db.reset_session(ticket["user_id"])

    delivered = await services.safe_send(
        bot, ticket["user_id"], texts.ticket_rejected_user(ticket_id, reason)
    )
    await message.answer(texts.admin_ticket_rejected(ticket_id))
    if not delivered:
        await message.answer(texts.ADMIN_DELIVERY_FAILED)

    log.info("Заявка %s отклонена", texts.tn(ticket_id))
