"""Настройки WAIP SHOP. Измени значения и сохрани файл.

Секреты лучше задавать переменными окружения (Render → Environment):
BOT_TOKEN, OWNER_ID, CRYPTOBOT_TOKEN, WEBAPP_URL, GAME_DB_HOST, GAME_DB_PORT,
GAME_DB_USER, GAME_DB_PASSWORD, GAME_DB_NAME. Значения ниже — запасные.
"""
import os

def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# 1. Токен от @BotFather
BOT_TOKEN = _env("BOT_TOKEN", "")
OWNER_ID = _env("OWNER_ID", "0")  # задайте свой Telegram ID в переменных окружения

# 2. Username для подарков Stars (fallback, если invoice XTR недоступен)
PAYMENT_DETAILS = "@mukhammad_im"
STARS_GIFT_USERNAME = "mukhammad_im"  # без @

# CryptoBot удалён из V3. Оплата только Telegram Stars.

# 4. Канал для обязательной подписки (без @)
CHANNEL_USERNAME = _env("CHANNEL_USERNAME", "")
CHANNEL_ID = int(_env("CHANNEL_ID", "0"))
CHANNEL_URL = _env("CHANNEL_URL", "")

# 5. Web App — HTTPS URL (обязательно для Mini App)
WEBAPP_URL = _env("WEBAPP_URL", "")
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = 8080

# 6. APK игры (прямой link на файл релиза GitHub)
# Пример: https://github.com/USER/REPO/releases/download/v1.0/game.apk
# latest:  https://github.com/USER/REPO/releases/latest/download/game.apk
APK_DOWNLOAD_URL = "https://github.com/USER/REPO/releases/latest/download/game.apk"
APK_BUTTON_TITLE = "Скачать игру"

# Премиум-эмодзи в сообщениях бота (обычно False)
PREMIUM_EMOJI = False

# 7. Курсы (цены в каталоге — в USD)
# 100 000 игровой валюты = 50 Telegram Stars; минимальный заказ — 50 Stars.
# Цены в каталоге задаются в USD как внутренняя единица пересчёта.
STARS_PER_USD = 50
MONEY_PER_USD = 100_000
# Минимальная покупка валюты (в игровой валюте). Максимума нет.
# Цена считается сама: units / MONEY_PER_USD (USD) → Stars.
MONEY_MIN_UNITS = 100_000

# Реферальная программа: +REF_PERCENT% рефереру за каждое оплаченное пополнение друга.
# При 100% — любой товар бесплатно.
REF_PERCENT = 5

# Ежедневный бонус валюты (раз в DAILY_BONUS_HOURS часов)
DAILY_BONUS_MONEY = 1_000_000
DAILY_BONUS_HOURS = 24

# 8. Хеш пароля в игровой БД
# plain | md5 | sha1 | sha256 | double_md5 | whirlpool
# Если после сброса вход не работает — смени метод под хеш вашего мода.
# Посмотри длину password в MySQL:
#   32 hex  → md5
#   40 hex  → sha1
#   64 hex  → sha256
#   128 hex → whirlpool
PASSWORD_HASH = _env("PASSWORD_HASH", "md5").strip().lower()

# 9. MySQL игрового сервера
GAME_DB_HOST = _env("GAME_DB_HOST", "")
GAME_DB_PORT = int(_env("GAME_DB_PORT", 3306))
GAME_DB_USER = _env("GAME_DB_USER", "")
GAME_DB_PASSWORD = _env("GAME_DB_PASSWORD", "")
GAME_DB_NAME = _env("GAME_DB_NAME", "")

GAME_ACCOUNT_TABLE = _env("GAME_ACCOUNT_TABLE", "accounts")
GAME_NICK_COLUMN = _env("GAME_NICK_COLUMN", "name")
GAME_LEVEL_COLUMN = _env("GAME_LEVEL_COLUMN", "level")
GAME_ADMIN_COLUMN = _env("GAME_ADMIN_COLUMN", "admin")
GAME_MONEY_COLUMN = _env("GAME_MONEY_COLUMN", "money")
GAME_SKIN_COLUMN = _env("GAME_SKIN_COLUMN", "skin")
GAME_PASSWORD_COLUMN = _env("GAME_PASSWORD_COLUMN", "password")
GAME_DONATE_COLUMN = _env("GAME_DONATE_COLUMN", "donate")  # для /give donate (если такого поля нет — команда вернёт ошибку)

# 10. Прайс-лист (price — в USD; Stars = price * STARS_PER_USD)
CATALOG = {
    "level": {
        "title": "Уровни",
        "icon": "level",
        "products": [
            {"id": "lvl1",  "title": "+1 уровень",   "kind": "level", "units": 1,  "price": 1},
            {"id": "lvl5",  "title": "+5 уровней",   "kind": "level", "units": 5,  "price": 4},
            {"id": "lvl10", "title": "+10 уровней",  "kind": "level", "units": 10, "price": 7},
        ],
    },
    "admin": {
        "title": "Админка",
        "icon": "admin",
        "products": [
            {"id": "adm1", "title": "Админка 1 уровня", "kind": "admin", "units": 1, "price": 2},
            {"id": "adm2", "title": "Админка 2 уровня", "kind": "admin", "units": 2, "price": 5},
            {"id": "adm3", "title": "Админка 3 уровня", "kind": "admin", "units": 3, "price": 10},
        ],
    },
    "skin": {
        "title": "Скины",
        "icon": "skin",
        "products": [
            {"id": "skin_admin", "title": "Скин админа (ID 122)", "kind": "skin", "units": 122, "price": 5},
        ],
    },
    "money": {
        "title": "Валюта",
        "icon": "money",
        "products": [],  # кастомная сумма в WebApp
        "custom": True,
        "rate_hint": "1 USD = 100 000 игровой валюты",
    },
    "password": {
        "title": "Сброс пароля",
        "icon": "password",
        "products": [
            {"id": "pwd_reset", "title": "Сброс пароля аккаунта", "kind": "password", "units": 1, "price": 1},
        ],
    },
}

# Курс покупки донат-валюты (отдельно от RUB и обычной игровой валюты)
DONATE_PER_10_STARS = 100  # 100 доната = 10 Telegram Stars
DONATE_MIN_UNITS = 100
