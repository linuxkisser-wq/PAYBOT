"""Все тексты бота. Голос — «Поддержка Nexora Pay», лаконично и по-дружески."""

from html import escape
from typing import Any, Optional

from config import TICKET_NUMBER_WIDTH

SUPPORT_NAME = "Поддержка Tortuga Cloud"

# Категории обращений: callback_data -> подпись.
REASONS: dict[str, str] = {
    "topup": "💰 Покупка подписки",
    "withdraw": "🌐 Проблемы с подключением",
    "exchange": "🔄 Что то не работает?",
    "deal": "⚠️ Проблема с серверами",
    "security": "🔐 Безопасность подключения",
    "other": "❓ Другое",
}

DIVIDER = "─────────────────────"

TOTAL_STEPS = 4


def esc(text: Optional[str]) -> str:
    return escape(text or "", quote=False)


def tn(ticket_id: int) -> str:
    """Номер заявки в виде #0001."""
    return f"#{ticket_id:0{TICKET_NUMBER_WIDTH}d}"


def progress(step: int) -> str:
    """Полоска прогресса: ▰▰▱▱ Шаг 2 из 4."""
    filled = "▰" * step
    empty = "▱" * (TOTAL_STEPS - step)
    return f"<code>{filled}{empty}</code>  Шаг {step} из {TOTAL_STEPS}"


def stars(rating: Optional[int]) -> str:
    if not rating:
        return "—"
    return "⭐" * rating + "☆" * (5 - rating)


# --- Пользователь ---------------------------------------------------------------

GREETING = (
    f"👋 Привет! Это <b>{SUPPORT_NAME}</b>.\n\n"
    "Поможем с пополнением, подключеним, исправлением ошибок.\n"
    "Обычно отвечаем в течение часа.\n\n"
    "Нажми кнопку ниже, чтобы создать заявку."
)


def choose_reason() -> str:
    return f"{progress(1)}\n\nВыбери причину обращения:"


def ask_description(reason: str) -> str:
    return (
        f"{progress(2)}\n\n"
        f"Причина: {esc(reason)}\n\n"
        "📝 Опиши проблему подробнее.\n"
        "Укажи детали, например: сервер, сумму, дату и т.д."
    )


def description_too_short(length: int, minimum: int) -> str:
    return (
        f"✏️ Слишком коротко — {length} из {minimum} символов.\n"
        "Добавь деталей, так мы решим вопрос быстрее."
    )


DESCRIPTION_NOT_TEXT = "Опиши проблему текстом — фото можно будет прикрепить на следующем шаге."


def ask_photo() -> str:
    return f"{progress(3)}\n\n🖼 Хочешь прикрепить скриншот? (необязательно)"


def waiting_photo() -> str:
    return f"{progress(3)}\n\n📎 Отправь одно фото сообщением."


PHOTO_EXPECTED = "Жду фото. Или нажми «Пропустить», если скриншота нет."

PHOTO_SAVED = "✅ Фото прикреплено."


def confirmation_card(ticket_id: int, reason: str, description: str, has_photo: bool) -> str:
    return (
        f"{progress(4)}\n\n"
        f"{DIVIDER}\n"
        f"📋 <b>ЗАЯВКА {tn(ticket_id)}</b>\n"
        f"{DIVIDER}\n"
        f"Причина: {esc(reason)}\n"
        f"Описание: {esc(description)}\n"
        f"Фото: {'есть' if has_photo else 'нет'}\n"
        f"{DIVIDER}\n\n"
        "Всё верно?"
    )


EDIT_MENU = "Что поправить?"


def ticket_sent(ticket_id: int) -> str:
    return (
        f"✅ Заявка {tn(ticket_id)} отправлена. Ожидай ответа от поддержки.\n\n"
        "⏳ Статус: заявка на рассмотрении.\n"
        "Проверить — /status"
    )


def ticket_accepted_user(ticket_id: int) -> str:
    return (
        f"✅ Ваша заявка {tn(ticket_id)} принята! Напишите сюда — поддержка ответит.\n\n"
        "💬 Активный чат с поддержкой."
    )


def ticket_rejected_user(ticket_id: int, reason: str) -> str:
    return (
        f"❌ Ваша заявка {tn(ticket_id)} отклонена.\n"
        f"Причина: {esc(reason)}\n\n"
        "Если хотите создать новую заявку — /start"
    )


def ticket_closed_user(ticket_id: int) -> str:
    return (
        f"🔒 Чат завершён. Тикет {tn(ticket_id)} закрыт. Спасибо за обращение!\n\n"
        "Новая заявка — /start"
    )


ASK_RATING = "Оцени, пожалуйста, работу поддержки — это займёт секунду:"


def rating_thanks(rating: int) -> str:
    tail = (
        "Спасибо! Рады, что помогли 🙌"
        if rating >= 4
        else "Спасибо за честность — разберёмся, что пошло не так."
    )
    return f"{stars(rating)}\n{tail}"


def ticket_expired_user(ticket_id: int, hours: int) -> str:
    return (
        f"⌛️ Заявка {tn(ticket_id)} не была рассмотрена в течение {hours} ч "
        "и закрыта автоматически.\n\n"
        "Извини за задержку. Создай новую заявку — /start"
    )


def already_open_ticket(ticket_id: int, status: str) -> str:
    return (
        f"У вас уже есть открытая заявка {tn(ticket_id)}.\n\n"
        f"{status_line(status)}\n"
        "Проверить — /status"
    )


def status_line(status: str) -> str:
    return {
        "pending": "⏳ Заявка на рассмотрении",
        "active": "💬 Активный чат с поддержкой",
        "closed": "🔒 Тикет закрыт",
        "rejected": "❌ Заявка отклонена",
        "expired": "⌛️ Заявка истекла",
    }.get(status, status)


def status_card(ticket: dict[str, Any]) -> str:
    lines = [
        DIVIDER,
        f"📋 <b>ЗАЯВКА {tn(ticket['id'])}</b>",
        DIVIDER,
        f"Причина: {esc(ticket['reason'])}",
        f"Создана: {esc(str(ticket['created_at']))} UTC",
        f"Статус: {status_line(ticket['status'])}",
    ]
    if ticket["status"] == "active" and ticket.get("accepted_at"):
        lines.append(f"Принята: {esc(str(ticket['accepted_at']))} UTC")
    if ticket.get("rating"):
        lines.append(f"Ваша оценка: {stars(ticket['rating'])}")
    lines.append(DIVIDER)
    return "\n".join(lines)


def history_card(tickets: list[dict[str, Any]]) -> str:
    lines = ["🗂 <b>Ваши последние заявки</b>", ""]
    for ticket in tickets:
        icon = {
            "pending": "⏳",
            "active": "💬",
            "closed": "🔒",
            "rejected": "❌",
            "expired": "⌛️",
        }.get(ticket["status"], "•")
        created = str(ticket["created_at"])[:10]
        rating = f" · {stars(ticket['rating'])}" if ticket.get("rating") else ""
        lines.append(f"{icon} {tn(ticket['id'])} · {created} · {esc(ticket['reason'])}{rating}")
    return "\n".join(lines)


NO_TICKETS = "У вас пока нет заявок. Создать — /start"

CANCELLED = "Создание заявки отменено. Начать заново — /start"

NOTHING_TO_CANCEL = "Сейчас нечего отменять. Создать заявку — /start"

CANT_CANCEL_ACTIVE = (
    "Заявка уже отправлена, отменить её нельзя.\n"
    "Дождись ответа поддержки или напиши прямо сюда — /status"
)

IDLE_HINT = "Чтобы обратиться в поддержку, создай заявку — /start"

USE_BUTTONS = "Выбери вариант кнопкой выше. Отменить — /cancel"

UNSUPPORTED_IN_CHAT = (
    "Поддерживаются текст и фото. Пришли, пожалуйста, в одном из этих форматов."
)


# --- Администратор --------------------------------------------------------------


def admin_new_ticket(ticket_id: int, username: Optional[str], user_id: int,
                     reason: str, description: str, has_photo: bool) -> str:
    handle = f"@{esc(username)}" if username else "без username"
    return (
        f"{DIVIDER}\n"
        f"🆕 <b>НОВАЯ ЗАЯВКА {tn(ticket_id)}</b>\n"
        f"{DIVIDER}\n"
        f"👤 Пользователь: {handle} (ID: <code>{user_id}</code>)\n"
        f"📁 Причина: {esc(reason)}\n"
        f"📝 Описание: {esc(description)}\n"
        f"🖼 Фото: {'прикреплено ниже' if has_photo else 'отсутствует'}\n"
        f"{DIVIDER}"
    )


def admin_ticket_accepted(ticket_id: int) -> str:
    return f"✅ Заявка {tn(ticket_id)} принята. Чат открыт."


def admin_ask_reject_reason(ticket_id: int) -> str:
    return (
        f"Укажи причину отклонения заявки {tn(ticket_id)} "
        "(будет отправлена пользователю):"
    )


def admin_ticket_rejected(ticket_id: int) -> str:
    return f"❌ Заявка {tn(ticket_id)} отклонена. Причина отправлена пользователю."


def admin_ticket_closed(ticket_id: int) -> str:
    return f"🔒 Тикет {tn(ticket_id)} закрыт."


def admin_ticket_expired(ticket_id: int, hours: int) -> str:
    return f"⌛️ Заявка {tn(ticket_id)} истекла ({hours} ч без ответа) и закрыта автоматически."


def admin_sla_ping(ticket_id: int, minutes: int, username: Optional[str]) -> str:
    handle = f"@{esc(username)}" if username else "пользователь без username"
    return (
        f"⏰ Заявка {tn(ticket_id)} ждёт уже {minutes} мин ({handle}).\n"
        "Карточка выше — прими или отклони."
    )


def admin_rating_received(ticket_id: int, rating: int) -> str:
    return f"{stars(rating)} — оценка по тикету {tn(ticket_id)}"


def admin_user_message(ticket_id: int, text: str) -> str:
    return f"[👤 User {tn(ticket_id)}]: {esc(text)}"


def admin_user_photo(ticket_id: int, caption: Optional[str]) -> str:
    body = f": {esc(caption)}" if caption else ""
    return f"[👤 User {tn(ticket_id)}] 🖼 фото{body}"


def admin_current_ticket_set(ticket_id: int) -> str:
    return f"💬 Текущий диалог: {tn(ticket_id)}. Всё, что напишешь — уйдёт пользователю."


ADMIN_NO_ACTIVE = (
    "Нет активного тикета. Ответы уходят пользователю только при открытом чате.\n"
    "Список активных — /tickets"
)

ADMIN_NO_TICKETS = "Открытых тикетов нет — ни ждущих, ни активных."

ADMIN_STOP_USAGE = "Укажи номер: /stop 1 — или открой диалог, чтобы закрыть текущий."


def admin_ticket_not_found(ticket_id: int) -> str:
    return f"Тикет {tn(ticket_id)} не найден."


def admin_ticket_not_active(ticket_id: int, status: str) -> str:
    return f"Тикет {tn(ticket_id)} не активен (статус: {status})."


def admin_tickets_list(tickets: list[dict[str, Any]], current: Optional[int]) -> str:
    pending = [t for t in tickets if t["status"] == "pending"]
    active = [t for t in tickets if t["status"] == "active"]

    def row(ticket: dict[str, Any], mark_current: bool) -> str:
        handle = f"@{esc(ticket['username'])}" if ticket["username"] else str(ticket["user_id"])
        mark = " ← текущий" if mark_current and ticket["id"] == current else ""
        return f"{tn(ticket['id'])} — {handle} — {esc(ticket['reason'])}{mark}"

    lines: list[str] = []
    if pending:
        lines.append("⏳ <b>Ждут решения:</b>")
        lines += [row(t, False) for t in pending]
        lines.append("")
    if active:
        lines.append("💬 <b>В работе:</b>")
        lines += [row(t, True) for t in active]
        lines.append("")

    lines.append("Переключиться: /reply 1 · Закрыть: /stop 1")
    return "\n".join(lines)


def admin_stats(data: dict[str, Any]) -> str:
    by_status = data["by_status"]
    lines = [
        "📊 <b>Статистика поддержки</b>",
        DIVIDER,
        f"Всего заявок: {data['total']}",
        f"⏳ Ждут: {by_status.get('pending', 0)}",
        f"💬 В работе: {by_status.get('active', 0)}",
        f"🔒 Закрыто: {by_status.get('closed', 0)}",
        f"❌ Отклонено: {by_status.get('rejected', 0)}",
        f"⌛️ Истекло: {by_status.get('expired', 0)}",
        DIVIDER,
    ]

    avg = data["avg_accept_minutes"]
    if avg is not None:
        lines.append(f"⚡️ Среднее время до принятия: {_humanize_minutes(avg)}")

    if data["ratings_count"]:
        lines.append(
            f"⭐ Средняя оценка: {data['avg_rating']:.1f} из 5 "
            f"({data['ratings_count']} оценок)"
        )
    else:
        lines.append("⭐ Оценок пока нет")

    if data["top_reasons"]:
        lines.append(DIVIDER)
        lines.append("<b>Частые причины:</b>")
        for reason, count in data["top_reasons"]:
            lines.append(f"  {esc(reason)} — {count}")

    return "\n".join(lines)


def _humanize_minutes(minutes: float) -> str:
    if minutes < 1:
        return "меньше минуты"
    if minutes < 60:
        return f"{minutes:.0f} мин"
    return f"{minutes / 60:.1f} ч"


ADMIN_HELP = (
    "<b>Панель поддержки Tortuga Cloud</b>\n\n"
    "Заявки приходят карточкой с кнопками «Принять» / «Отклонить».\n\n"
    "/tickets — активные тикеты\n"
    "/stats — статистика\n"
    "/reply 1 — сделать тикет текущим\n"
    "/stop [1] — закрыть тикет (без номера — текущий)\n\n"
    "Ответ на пересланное сообщение (reply) уходит в тот же тикет.\n"
    "Обычное сообщение уходит в текущий диалог."
)

ADMIN_DELIVERY_FAILED = "⚠️ Не удалось доставить сообщение пользователю (возможно, он заблокировал бота)."
