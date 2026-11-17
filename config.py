"""Настройки WAIP SHOP. Измени значения и сохрани файл."""

# 1. Токен от @BotFather
BOT_TOKEN = "8878110137:AAFZFjXNJMjRNe6uSVKU7gOrAwT4WgEFWo0"
OWNER_ID = "9292798672"  # Telegram ID владельца бота

# 2. Username для подарков Stars (fallback, если invoice XTR недоступен)
PAYMENT_DETAILS = "@mukhammad_im"
STARS_GIFT_USERNAME = "mukhammad_im"  # без @

# 3. CryptoBot API Token (от @CryptoBot → Crypto Pay → Create App)
CRYPTOBOT_TOKEN = ""

# 4. Канал для обязательной подписки (без @)
CHANNEL_USERNAME = ""
CHANNEL_ID = 0

# 5. Web App — HTTPS URL (обязательно для Mini App)
WEBAPP_URL = ""
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = 8080

# 6. APK игры (прямой link на файл релиза GitHub)
# Пример: https://github.com/USER/REPO/releases/download/v1.0/game.apk
# latest:  https://github.com/USER/REPO/releases/latest/download/game.apk
APK_DOWNLOAD_URL = "https://github.com/USER/REPO/releases/latest/download/game.apk"
APK_BUTTON_TITLE = "Скачать игру"

# 7. Курсы (цены в каталоге — в USD)
# 1 USD = STARS_PER_USD Telegram Stars
# 1 USD = MONEY_PER_USD игровой валюты
STARS_PER_USD = 50
MONEY_PER_USD = 100_000
# Минимальная сумма покупки валюты в USD
MONEY_MIN_USD = 1

# 8. Хеш пароля в игровой БД
# plain | md5 | sha1 | sha256 | double_md5 | whirlpool
# Если после сброса вход не работает — смени метод под хеш вашего мода.
# Посмотри длину password в MySQL:
#   32 hex  → md5
#   40 hex  → sha1
#   64 hex  → sha256
#   128 hex → whirlpool
PASSWORD_HASH = "md5"

# 9. MySQL игрового сервера
GAME_DB_HOST = "178.130.53.37"
GAME_DB_PORT = 3306
GAME_DB_USER = "gs365018"
GAME_DB_PASSWORD = "Zy1HZ8OiIVca"
GAME_DB_NAME = "gs365018"

GAME_ACCOUNT_TABLE = "accounts"
GAME_NICK_COLUMN = "name"
GAME_LEVEL_COLUMN = "level"
GAME_ADMIN_COLUMN = "admin"
GAME_MONEY_COLUMN = "money"
GAME_SKIN_COLUMN = "skin"
GAME_PASSWORD_COLUMN = "password"

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
