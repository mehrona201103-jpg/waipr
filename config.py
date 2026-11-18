"""Настройки WAIP SHOP. Все секреты и настройки — только через переменные окружения
(Render → Environment). Здесь нет ни токенов, ни паролей."""
import os


def _env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# ── Бот и владелец ───────────────────────────────────────────────────────────
BOT_TOKEN = _env("BOT_TOKEN")
OWNER_ID = _env("OWNER_ID", "0")

# master — основной магазин, который создаёт дочерние боты
# child  — дочерний магазин, запущенный мастером (не создаёт ботов сам)
ROLE = _env("WAIP_ROLE", "master")

# Параметры дочернего магазина (заполняет мастер при запуске)
TENANT_ID = _env("TENANT_ID", "")
TENANT_REPORT_URL = _env("TENANT_REPORT_URL", "")   # http://127.0.0.1:PORT/internal/sale
TENANT_SECRET = _env("TENANT_SECRET", "")
SHOP_TITLE = _env("SHOP_TITLE", "WAIP SHOP")
SHOP_THEME = _env("SHOP_THEME", "")                # #RRGGBB
SHOP_LOGO_PATH = _env("SHOP_LOGO_PATH", "")

# Username для подарков Stars (fallback, если invoice недоступен). Пусто = не показывать.
STARS_GIFT_USERNAME = _env("STARS_GIFT_USERNAME", "")

# ── Канал для обязательной подписки ─────────────────────────────────────────
CHANNEL_USERNAME = _env("CHANNEL_USERNAME", "").lstrip("@")
CHANNEL_ID = int(_env("CHANNEL_ID", "0") or 0)
CHANNEL_URL = _env("CHANNEL_URL", "")

# ── Web App ─────────────────────────────────────────────────────────────────
WEBAPP_URL = _env("WEBAPP_URL", "")                 # https://сервис.onrender.com/webapp/
WEBAPP_HOST = _env("WEBAPP_HOST", "0.0.0.0")
# Render задаёт PORT автоматически
WEBAPP_PORT = int(_env("WEBAPP_PORT", _env("PORT", "8080")))
TENANT_PORT_BASE = int(_env("TENANT_PORT_BASE", "18000"))


def _pick_dir(preferred: str, fallback: str) -> str:
    """Первая папка, в которую можно писать. Если диск недоступен — запасная (без сохранности)."""
    for d in (preferred, fallback):
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return d
        except OSError:
            continue
    return fallback


_FALLBACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_db_env = _env("DB_PATH", "")
# /var/data — Persistent Disk (платный план). На бесплатном плане его нет → запасная папка.
DATA_DIR = _pick_dir(os.path.dirname(_db_env) if _db_env else _env("DATA_DIR", "/var/data"), _FALLBACK_DIR)
DATA_PERSISTENT = DATA_DIR != _FALLBACK_DIR and os.path.isdir("/var/data") and DATA_DIR.startswith("/var/data")
DB_PATH = os.path.join(DATA_DIR, os.path.basename(_db_env) or "orders.sqlite3")
TENANTS_DIR = os.path.join(DATA_DIR, "tenants")

# Бесплатные бэкапы: id ПРИВАТНОГО канала (вида -100...), где бот админ с правом закреплять и удалять сообщения
BACKUP_CHAT_ID = int(_env("BACKUP_CHAT_ID", "0") or 0)

APK_DOWNLOAD_URL = _env("APK_DOWNLOAD_URL", "")     # пусто = кнопка скрыта
APK_BUTTON_TITLE = "Скачать игру"

PREMIUM_EMOJI = False

# ── Экономика (всё в Telegram Stars) ────────────────────────────────────────
# 2000 игровой валюты = 1 Star → 100 000 валюты = 50 Stars
MONEY_PER_STAR = int(_env("MONEY_PER_STAR", "2000"))
MONEY_MIN_UNITS = int(_env("MONEY_MIN_UNITS", "100000"))

# Реферальная программа: +REF_PERCENT% рефереру за каждое оплаченное пополнение друга
REF_PERCENT = 5

DAILY_BONUS_MONEY = 1_000_000
DAILY_BONUS_HOURS = 24

# Хеш пароля в игровой БД: plain | md5 | sha1 | sha256 | double_md5 | whirlpool
PASSWORD_HASH = _env("PASSWORD_HASH", "md5").strip().lower()

# ── MySQL игрового сервера ──────────────────────────────────────────────────
GAME_DB_HOST = _env("GAME_DB_HOST", "")
GAME_DB_PORT = int(_env("GAME_DB_PORT", "3306"))
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
GAME_DONATE_COLUMN = _env("GAME_DONATE_COLUMN", "donate")

# ── Каталог. price — цена в Telegram Stars ──────────────────────────────────
CATALOG = {
    "level": {
        "title": "Уровни",
        "icon": "level",
        "products": [
            {"id": "lvl1", "title": "+1 уровень", "kind": "level", "units": 1, "price": 50},
            {"id": "lvl5", "title": "+5 уровней", "kind": "level", "units": 5, "price": 200},
            {"id": "lvl10", "title": "+10 уровней", "kind": "level", "units": 10, "price": 350},
        ],
    },
    "admin": {
        "title": "Админка",
        "icon": "admin",
        "products": [
            {"id": "adm1", "title": "Админка 1 уровня", "kind": "admin", "units": 1, "price": 100},
            {"id": "adm2", "title": "Админка 2 уровня", "kind": "admin", "units": 2, "price": 250},
            {"id": "adm3", "title": "Админка 3 уровня", "kind": "admin", "units": 3, "price": 500},
        ],
    },
    "skin": {
        "title": "Скины",
        "icon": "skin",
        "products": [
            {"id": "skin_admin", "title": "Скин админа (ID 122)", "kind": "skin", "units": 122, "price": 250},
        ],
    },
    "money": {
        "title": "Валюта",
        "icon": "money",
        "products": [],          # сумма вводится в WebApp / боте
        "custom": True,
        "rate_hint": "2 000 валюты = 1 Star",
    },
    "password": {
        "title": "Сброс пароля",
        "icon": "password",
        "products": [
            {"id": "pwd_reset", "title": "Сброс пароля аккаунта", "kind": "password", "units": 1, "price": 50},
        ],
    },
}
