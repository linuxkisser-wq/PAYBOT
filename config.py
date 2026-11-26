"""Конфигурация бота: читается из .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent

load_dotenv(BASE_DIR / ".env")


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Переменная {name} не задана. Скопируй .env.example в .env и заполни."
        )
    return value


BOT_TOKEN: str = _require("BOT_TOKEN")

try:
    ADMIN_CHAT_ID: int = int(_require("ADMIN_CHAT_ID"))
except ValueError as exc:
    raise RuntimeError("ADMIN_CHAT_ID должен быть числом (например 123456789).") from exc

# Прокси до api.telegram.org. Если не задан явно — берём системный из окружения
# (aiohttp, в отличие от pip и браузера, сам его не подхватывает).
PROXY_URL: str | None = (
    os.getenv("PROXY_URL")
    or os.getenv("HTTPS_PROXY")
    or os.getenv("https_proxy")
    or os.getenv("ALL_PROXY")
    or os.getenv("all_proxy")
    or None
)

DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()
LOG_FILE: Path = BASE_DIR / os.getenv("LOG_FILE", "bot.log")

TICKET_TIMEOUT_HOURS: int = int(os.getenv("TICKET_TIMEOUT_HOURS", "24"))

# Как часто фоновой задаче проверять просроченные заявки.
TIMEOUT_CHECK_INTERVAL_SECONDS: int = 60

MIN_DESCRIPTION_LENGTH: int = 10

# Нумерация с нуля: counter стартует с 0, первый тикет — #0001.
TICKET_COUNTER_START: int = 0

# Ширина номера при выводе: 1 -> #0001.
TICKET_NUMBER_WIDTH: int = 4

# Через сколько минут напомнить админу о нерассмотренной заявке.
SLA_PING_MINUTES: int = 30
