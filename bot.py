"""Точка входа: Telegram-бот поддержки Nexora Pay.

Запуск:  python bot.py
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

import db
import services
import texts
from config import (
    ADMIN_CHAT_ID,
    BOT_TOKEN,
    LOG_FILE,
    PROXY_URL,
    SLA_PING_MINUTES,
    TICKET_TIMEOUT_HOURS,
    TIMEOUT_CHECK_INTERVAL_SECONDS,
)
from handlers import admin, user

log = logging.getLogger("nexora")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def timeout_worker(bot: Bot) -> None:
    """Раз в минуту: напоминания по SLA и закрытие просроченных заявок.

    Переживает рестарт: состояние берётся из БД, а не из таймеров в памяти.
    """
    while True:
        try:
            # Досылаем карточки, которые не дошли: иначе заявка висит невидимой
            # для админа, хотя пользователю уже сказали «отправлено».
            for ticket in await db.get_undelivered_pending():
                try:
                    await services.deliver_ticket_to_admin(bot, ticket)
                    log.info("Карточка %s доставлена повторно", texts.tn(ticket["id"]))
                except TelegramAPIError as exc:
                    log.warning("Повтор доставки %s не удался: %s", texts.tn(ticket["id"]), exc)

            for ticket in await db.get_sla_overdue(SLA_PING_MINUTES):
                await services.ping_sla(bot, ticket, SLA_PING_MINUTES)

            for ticket in await db.get_expired_pending(TICKET_TIMEOUT_HOURS):
                await services.expire_ticket(bot, ticket)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в фоновой проверке заявок")
        await asyncio.sleep(TIMEOUT_CHECK_INTERVAL_SECONDS)


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню / создать заявку"),
            BotCommand(command="status", description="Статус текущей заявки"),
            BotCommand(command="history", description="Мои последние заявки"),
            BotCommand(command="cancel", description="Отменить создание заявки"),
        ],
        scope=BotCommandScopeDefault(),
    )
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="tickets", description="Активные тикеты"),
                BotCommand(command="stats", description="Статистика поддержки"),
                BotCommand(command="reply", description="Сделать тикет текущим: /reply 1001"),
                BotCommand(command="stop", description="Закрыть тикет"),
                BotCommand(command="help", description="Справка по панели"),
            ],
            scope=BotCommandScopeChat(chat_id=ADMIN_CHAT_ID),
        )
    except TelegramAPIError as exc:
        log.warning("Команды для админского чата не установлены: %s", exc)


async def main() -> None:
    setup_logging()
    await db.init()

    if PROXY_URL:
        log.info("Telegram API через прокси: %s", PROXY_URL)
    bot = Bot(
        token=BOT_TOKEN,
        session=AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    # Порядок важен: админский роутер отфильтрован по чату и идёт первым.
    dispatcher.include_router(admin.router)
    dispatcher.include_router(user.router)

    me = await bot.get_me()
    log.info("Запуск @%s | админский чат: %s", me.username, ADMIN_CHAT_ID)

    await set_commands(bot)
    worker = asyncio.create_task(timeout_worker(bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        await db.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
