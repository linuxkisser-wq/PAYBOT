"""Inline-клавиатуры. Reply-кнопок в боте нет вообще."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from texts import REASONS


def start_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📩 Создать заявку", callback_data="new_ticket")]]
    )


def reasons_menu() -> InlineKeyboardMarkup:
    """Каждая причина — отдельной строкой."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"reason:{key}")]
            for key, label in REASONS.items()
        ]
    )


def photo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📎 Прикрепить фото", callback_data="photo:attach"),
                InlineKeyboardButton(text="➡️ Пропустить", callback_data="photo:skip"),
            ]
        ]
    )


def confirm_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить заявку", callback_data="confirm:send"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="confirm:edit"),
            ]
        ]
    )


def edit_menu(has_photo: bool) -> InlineKeyboardMarkup:
    """Точечная правка одного поля вместо прохода всего флоу заново."""
    photo_label = "🖼 Заменить фото" if has_photo else "🖼 Добавить фото"
    rows = [
        [InlineKeyboardButton(text="📁 Причину", callback_data="edit:reason")],
        [InlineKeyboardButton(text="📝 Описание", callback_data="edit:desc")],
        [InlineKeyboardButton(text=photo_label, callback_data="edit:photo")],
    ]
    if has_photo:
        rows.append([InlineKeyboardButton(text="🗑 Убрать фото", callback_data="edit:photo_drop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к заявке", callback_data="edit:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rating_menu(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐" * n, callback_data=f"rate:{ticket_id}:{n}")
                for n in range(1, 4)
            ],
            [
                InlineKeyboardButton(text="⭐" * n, callback_data=f"rate:{ticket_id}:{n}")
                for n in range(4, 6)
            ],
        ]
    )


def pending_tickets_menu(tickets: list[dict]) -> InlineKeyboardMarkup | None:
    """Кнопки «показать карточку» для ждущих заявок — путь восстановления,
    если исходная карточка не дошла или её удалили."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"📨 Открыть #{ticket['id']:04d}",
                callback_data=f"admin:card:{ticket['id']}",
            )
        ]
        for ticket in tickets
        if ticket["status"] == "pending"
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def admin_ticket_menu(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"admin:accept:{ticket_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:reject:{ticket_id}"),
            ]
        ]
    )
