"""Пользовательская часть: FSM создания заявки и активный чат с поддержкой."""

import logging

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

import db
import keyboards
import services
import texts
from config import ADMIN_CHAT_ID, MIN_DESCRIPTION_LENGTH

log = logging.getLogger(__name__)

router = Router(name="user")

# Всё, что не из админского чата и приходит в личку.
router.message.filter(F.chat.type == ChatType.PRIVATE, F.chat.id != ADMIN_CHAT_ID)
router.callback_query.filter(F.message.chat.id != ADMIN_CHAT_ID)

DRAFT_STATES = {
    db.STATE_CHOOSE_REASON,
    db.STATE_DESCRIBE,
    db.STATE_ATTACH_PHOTO,
    db.STATE_CONFIRM,
    db.STATE_EDIT_REASON,
    db.STATE_EDIT_DESC,
    db.STATE_EDIT_PHOTO,
}


async def _show_reasons(message: Message, user_id: int, edit: bool, state: str) -> None:
    await db.update_session(user_id, state=state)
    text = texts.choose_reason()
    markup = keyboards.reasons_menu()
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _show_confirmation(message: Message, user_id: int, edit: bool) -> None:
    """Показать итоговую карточку. Номер выдаётся один раз и переживает «Изменить»."""
    session = await db.get_session(user_id)
    ticket_id = session["ticket_id"]
    if ticket_id is None:
        ticket_id = await db.next_ticket_id()
        await db.update_session(user_id, ticket_id=ticket_id)

    await db.update_session(user_id, state=db.STATE_CONFIRM)

    card = texts.confirmation_card(
        ticket_id=ticket_id,
        reason=session["temp_reason"],
        description=session["temp_desc"],
        has_photo=bool(session["temp_photo"]),
    )
    if edit:
        await message.edit_text(card, reply_markup=keyboards.confirm_menu())
    else:
        await message.answer(card, reply_markup=keyboards.confirm_menu())


# --- Команды --------------------------------------------------------------------


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id
    open_ticket = await db.get_open_ticket_for_user(user_id)
    if open_ticket:
        await message.answer(
            texts.already_open_ticket(open_ticket["id"], open_ticket["status"])
        )
        return

    await db.reset_session(user_id)
    await message.answer(texts.GREETING, reply_markup=keyboards.start_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    user_id = message.from_user.id
    session = await db.get_session(user_id)

    if session["state"] in DRAFT_STATES:
        await db.reset_session(user_id)
        await message.answer(texts.CANCELLED)
        return

    open_ticket = await db.get_open_ticket_for_user(user_id)
    if open_ticket:
        await message.answer(texts.CANT_CANCEL_ACTIVE)
        return

    await message.answer(texts.NOTHING_TO_CANCEL)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    user_id = message.from_user.id
    ticket = await db.get_open_ticket_for_user(user_id) or await db.get_last_ticket_for_user(user_id)
    if ticket is None:
        await message.answer(texts.NO_TICKETS)
        return
    await message.answer(texts.status_card(ticket))


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    tickets = await db.get_user_history(message.from_user.id, limit=5)
    if not tickets:
        await message.answer(texts.NO_TICKETS)
        return
    await message.answer(texts.history_card(tickets))


# --- Шаг 1-2: причина -----------------------------------------------------------


@router.callback_query(F.data == "new_ticket")
async def cb_new_ticket(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    open_ticket = await db.get_open_ticket_for_user(user_id)
    if open_ticket:
        await callback.answer(
            f"У вас уже есть заявка {texts.tn(open_ticket['id'])}", show_alert=True
        )
        return

    await _show_reasons(callback.message, user_id, edit=True, state=db.STATE_CHOOSE_REASON)
    await callback.answer()


@router.callback_query(F.data.startswith("reason:"))
async def cb_reason(callback: CallbackQuery) -> None:
    """Выбор причины — и в основном флоу, и при точечной правке."""
    user_id = callback.from_user.id
    key = callback.data.split(":", 1)[1]
    label = texts.REASONS.get(key)
    if label is None:
        await callback.answer("Неизвестная причина", show_alert=True)
        return

    session = await db.get_session(user_id)
    await db.update_session(user_id, temp_reason=label)

    # Правка причины возвращает сразу к карточке, а не гонит по всем шагам.
    if session["state"] == db.STATE_EDIT_REASON:
        await _show_confirmation(callback.message, user_id, edit=True)
        await callback.answer("Причина обновлена")
        return

    await db.update_session(user_id, state=db.STATE_DESCRIBE)
    await callback.message.edit_text(texts.ask_description(label))
    await callback.answer()


# --- Шаг 4: фото ----------------------------------------------------------------


@router.callback_query(F.data == "photo:attach")
async def cb_photo_attach(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    await db.update_session(user_id, state=db.STATE_ATTACH_PHOTO)
    await callback.message.edit_text(texts.waiting_photo())
    await callback.answer()


@router.callback_query(F.data == "photo:skip")
async def cb_photo_skip(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    await db.update_session(user_id, temp_photo=None)
    await _show_confirmation(callback.message, user_id, edit=True)
    await callback.answer()


# --- Шаг 5: подтверждение и точечная правка -------------------------------------


@router.callback_query(F.data == "confirm:edit")
async def cb_confirm_edit(callback: CallbackQuery) -> None:
    session = await db.get_session(callback.from_user.id)
    await callback.message.edit_text(
        texts.EDIT_MENU, reply_markup=keyboards.edit_menu(bool(session["temp_photo"]))
    )
    await callback.answer()


@router.callback_query(F.data == "edit:back")
async def cb_edit_back(callback: CallbackQuery) -> None:
    await _show_confirmation(callback.message, callback.from_user.id, edit=True)
    await callback.answer()


@router.callback_query(F.data == "edit:reason")
async def cb_edit_reason(callback: CallbackQuery) -> None:
    await _show_reasons(
        callback.message, callback.from_user.id, edit=True, state=db.STATE_EDIT_REASON
    )
    await callback.answer()


@router.callback_query(F.data == "edit:desc")
async def cb_edit_desc(callback: CallbackQuery) -> None:
    await db.update_session(callback.from_user.id, state=db.STATE_EDIT_DESC)
    await callback.message.edit_text("📝 Пришли новое описание одним сообщением.")
    await callback.answer()


@router.callback_query(F.data == "edit:photo")
async def cb_edit_photo(callback: CallbackQuery) -> None:
    await db.update_session(callback.from_user.id, state=db.STATE_EDIT_PHOTO)
    await callback.message.edit_text("🖼 Пришли фото одним сообщением.")
    await callback.answer()


@router.callback_query(F.data == "edit:photo_drop")
async def cb_edit_photo_drop(callback: CallbackQuery) -> None:
    await db.update_session(callback.from_user.id, temp_photo=None)
    await _show_confirmation(callback.message, callback.from_user.id, edit=True)
    await callback.answer("Фото убрано")


@router.callback_query(F.data == "confirm:send")
async def cb_confirm_send(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    session = await db.get_session(user_id)

    if session["state"] != db.STATE_CONFIRM or not session["temp_desc"]:
        await callback.answer("Заявка устарела. Начни заново — /start", show_alert=True)
        return

    open_ticket = await db.get_open_ticket_for_user(user_id)
    if open_ticket:
        await callback.answer(
            f"У вас уже есть заявка {texts.tn(open_ticket['id'])}", show_alert=True
        )
        return

    ticket_id = session["ticket_id"]
    try:
        await db.create_ticket(
            ticket_id=ticket_id,
            user_id=user_id,
            username=callback.from_user.username,
            reason=session["temp_reason"],
            description=session["temp_desc"],
            photo_id=session["temp_photo"],
        )
    except aiosqlite.IntegrityError:
        # Двойной тап по «Отправить»: заявка уже создана первым нажатием.
        await callback.answer(f"Заявка {texts.tn(ticket_id)} уже отправлена", show_alert=True)
        return

    await db.update_session(
        user_id, state=db.STATE_IDLE, temp_reason=None, temp_desc=None, temp_photo=None
    )

    await callback.message.edit_text(texts.ticket_sent(ticket_id))
    await callback.answer()

    ticket = await db.get_ticket(ticket_id)
    try:
        await services.deliver_ticket_to_admin(bot, ticket)
    except TelegramAPIError as exc:
        log.error("Заявка %s не доставлена админу: %s", texts.tn(ticket_id), exc)

    log.info("Создана заявка %s от user_id=%s", texts.tn(ticket_id), user_id)


# --- Шаг 7: оценка после закрытия -----------------------------------------------


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(callback: CallbackQuery, bot: Bot) -> None:
    _, raw_ticket, raw_rating = callback.data.split(":")
    ticket_id, rating = int(raw_ticket), int(raw_rating)

    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["user_id"] != callback.from_user.id:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    if ticket["rating"]:
        await callback.answer("Оценка уже сохранена", show_alert=True)
        return

    await db.set_rating(ticket_id, rating)
    await callback.message.edit_text(texts.rating_thanks(rating))
    await callback.answer("Спасибо!")

    await services.safe_send(bot, ADMIN_CHAT_ID, texts.admin_rating_received(ticket_id, rating))
    log.info("Тикет %s оценён на %s", texts.tn(ticket_id), rating)


# --- Шаг 3: описание, шаг 4: приём фото, шаг 6: активный чат ---------------------


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    session = await db.get_session(user_id)
    state = session["state"]

    if state in (db.STATE_ATTACH_PHOTO, db.STATE_EDIT_PHOTO):
        # Берём самый крупный размер.
        file_id = message.photo[-1].file_id
        await db.update_session(user_id, temp_photo=file_id)
        await message.answer(texts.PHOTO_SAVED)
        await _show_confirmation(message, user_id, edit=False)
        return

    if state == db.STATE_ACTIVE and session["ticket_id"]:
        await _relay_to_admin(message, bot, session["ticket_id"], photo=True)
        return

    if state in (db.STATE_DESCRIBE, db.STATE_EDIT_DESC):
        await message.answer(texts.DESCRIPTION_NOT_TEXT)
        return

    await _idle_reply(message, user_id)


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    session = await db.get_session(user_id)
    state = session["state"]

    if state in (db.STATE_DESCRIBE, db.STATE_EDIT_DESC):
        text = message.text.strip()
        if len(text) < MIN_DESCRIPTION_LENGTH:
            await message.answer(
                texts.description_too_short(len(text), MIN_DESCRIPTION_LENGTH)
            )
            return
        await db.update_session(user_id, temp_desc=text)

        if state == db.STATE_EDIT_DESC:
            await _show_confirmation(message, user_id, edit=False)
            return

        await db.update_session(user_id, state=db.STATE_ATTACH_PHOTO)
        await message.answer(texts.ask_photo(), reply_markup=keyboards.photo_menu())
        return

    if state in (db.STATE_ATTACH_PHOTO, db.STATE_EDIT_PHOTO):
        await message.answer(texts.PHOTO_EXPECTED, reply_markup=keyboards.photo_menu())
        return

    if state == db.STATE_ACTIVE and session["ticket_id"]:
        await _relay_to_admin(message, bot, session["ticket_id"], photo=False)
        return

    if state in (db.STATE_CHOOSE_REASON, db.STATE_EDIT_REASON, db.STATE_CONFIRM):
        await message.answer(texts.USE_BUTTONS)
        return

    await _idle_reply(message, user_id)


@router.message()
async def on_other(message: Message) -> None:
    session = await db.get_session(message.from_user.id)
    if session["state"] == db.STATE_ACTIVE:
        await message.answer(texts.UNSUPPORTED_IN_CHAT)
        return
    await _idle_reply(message, message.from_user.id)


async def _idle_reply(message: Message, user_id: int) -> None:
    """Сообщение вне флоу: подсказать статус или предложить создать заявку."""
    ticket = await db.get_open_ticket_for_user(user_id)
    if ticket and ticket["status"] == db.STATUS_PENDING:
        await message.answer(texts.status_line(db.STATUS_PENDING))
        return
    if message.text and message.text.startswith("/"):
        return
    await message.answer(texts.IDLE_HINT)


async def _relay_to_admin(message: Message, bot: Bot, ticket_id: int, photo: bool) -> None:
    """Шаг 6: сообщение пользователя уходит админу с пометкой [👤 User #XXXX]."""
    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["status"] != db.STATUS_ACTIVE:
        await db.reset_session(message.from_user.id)
        await message.answer(texts.IDLE_HINT)
        return

    try:
        if photo:
            sent = await bot.send_photo(
                chat_id=ADMIN_CHAT_ID,
                photo=message.photo[-1].file_id,
                caption=texts.admin_user_photo(ticket_id, message.caption),
            )
        else:
            sent = await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=texts.admin_user_message(ticket_id, message.text),
            )
    except TelegramAPIError as exc:
        log.error("Сообщение по тикету %s не доставлено админу: %s", texts.tn(ticket_id), exc)
        await message.answer("⚠️ Не удалось отправить. Попробуй ещё раз через минуту.")
        return

    await db.map_admin_msg(sent.message_id, ticket_id)

    # Ненавязчивый сигнал «сообщение ушло, поддержка его видит».
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    except TelegramAPIError:
        pass

    # Если у админа нет открытого диалога — этот становится текущим.
    admin_state = await db.get_admin_state(ADMIN_CHAT_ID)
    if admin_state["current_ticket_id"] is None:
        await db.update_admin_state(ADMIN_CHAT_ID, current_ticket_id=ticket_id)
