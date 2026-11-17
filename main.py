"""WAIP SHOP — авто-выдача level / admin / skin / money / password reset.

Запуск: python3 main.py
Настройки в config.py. Цены, модераторы, промокоды — через админку.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import string
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import config as C
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    WebAppInfo,
)

BASE = Path(__file__).resolve().parent
try:
    os.umask(0o077)
except OSError:
    pass

BOT_TOKEN = C.BOT_TOKEN.strip()
OWNER_ID = int(C.OWNER_ID)
PAYMENT_DETAILS = C.PAYMENT_DETAILS
STARS_USER = getattr(C, "STARS_GIFT_USERNAME", "mukhammad_im").lstrip("@")
CRYPTOBOT_TOKEN = (getattr(C, "CRYPTOBOT_TOKEN", "") or "").strip()
WEBAPP_URL = (getattr(C, "WEBAPP_URL", "") or "").strip().rstrip("/")
WEBAPP_HOST = getattr(C, "WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(getattr(C, "WEBAPP_PORT", 8080) or 8080)
APK_DOWNLOAD_URL = (getattr(C, "APK_DOWNLOAD_URL", "") or "").strip()
APK_BUTTON_TITLE = getattr(C, "APK_BUTTON_TITLE", "📥 Скачать игру (APK)")
CHANNEL_USERNAME = (getattr(C, "CHANNEL_USERNAME", "") or "").lstrip("@")
CHANNEL_ID = int(getattr(C, "CHANNEL_ID", 0) or 0)
PREMIUM = bool(C.PREMIUM_EMOJI)
DB_PATH = str(BASE / "orders.sqlite3")
CATALOG = C.CATALOG

ALLOWED_KINDS = ("level", "admin", "money", "skin", "password")

STARS_PER_USD = int(getattr(C, "STARS_PER_USD", 50) or 50)
MONEY_PER_USD = int(getattr(C, "MONEY_PER_USD", 100_000) or 100_000)
MONEY_MIN_USD = float(getattr(C, "MONEY_MIN_USD", 1) or 1)
PASSWORD_HASH = (getattr(C, "PASSWORD_HASH", "md5") or "md5").strip().lower()

PRODUCTS: dict[str, dict] = {}
for _cat, _sec in CATALOG.items():
    for _item in _sec.get("products") or []:
        code = _item["id"]
        if code in PRODUCTS or _item["kind"] not in ALLOWED_KINDS:
            raise ValueError(f"Ошибка config.py: товар {code}")
        if not (0 < float(_item["price"]) < 10**8 and 0 < int(_item["units"]) < 10**10):
            raise ValueError(f"Ошибка config.py: цена/units у {code}")
        PRODUCTS[code] = dict(_item, category=_cat)


class GrantError(Exception):
    pass


def identifier(s: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s or ""):
        raise GrantError(f"Недопустимое имя: {s!r}")
    return f"`{s}`"


def gen_password(length: int = 8) -> str:
    """8 символов: буквы + цифры вперемешку."""
    if length < 4:
        length = 8
    letters = string.ascii_letters
    digits = string.digits
    chars = [
        random.choice(letters), random.choice(letters),
        random.choice(digits), random.choice(digits),
    ]
    pool = letters + digits
    chars += [random.choice(pool) for _ in range(length - 4)]
    random.shuffle(chars)
    return "".join(chars)


def hash_password(plain: str) -> str:
    """Хеширует пароль под формат игровой БД (см. PASSWORD_HASH в config).

    Поддерживается: plain, md5, sha1, sha256, double_md5, whirlpool.
    Для WHIRLPOOL нужен hashlib с поддержкой (OpenSSL) — иначе fallback md5.
    """
    raw = (plain or "").encode("utf-8")
    method = PASSWORD_HASH
    if method in ("plain", "none", ""):
        return plain
    if method == "md5":
        return hashlib.md5(raw).hexdigest()
    if method == "sha1":
        return hashlib.sha1(raw).hexdigest()
    if method == "sha256":
        return hashlib.sha256(raw).hexdigest()
    if method in ("double_md5", "md5md5"):
        return hashlib.md5(hashlib.md5(raw).hexdigest().encode()).hexdigest()
    if method in ("whirlpool", "wp"):
        try:
            return hashlib.new("whirlpool", raw).hexdigest()
        except Exception:
            logging.exception("whirlpool unavailable")
            raise GrantError("Хеш whirlpool недоступен на сервере Python/OpenSSL")
    logging.warning("Unknown PASSWORD_HASH=%s, using md5", method)
    return hashlib.md5(raw).hexdigest()


def usd_to_stars(usd: float) -> int:
    return max(1, int(round(float(usd) * STARS_PER_USD)))


def money_from_usd(usd: float) -> int:
    return int(round(float(usd) * MONEY_PER_USD))


def usd_from_money(units: int) -> float:
    return round(float(units) / MONEY_PER_USD, 4)


@dataclass(frozen=True)
class GameConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    table: str
    nickname_col: str
    level_col: str
    admin_col: str
    money_col: str
    skin_col: str
    password_col: str

    def check(self):
        for x in (
            self.table, self.nickname_col, self.level_col, self.admin_col,
            self.money_col, self.skin_col, self.password_col,
        ):
            identifier(x)
        if not self.host or not self.user or not self.database:
            raise GrantError("Не настроены MySQL хост / пользователь / база")


def grant_sql(order: dict, cfg: GameConfig, new_password: str | None = None):
    kind = order["kind"]
    amount = int(order["units"])
    table, nick = identifier(cfg.table), identifier(cfg.nickname_col)

    if kind == "password":
        if not new_password:
            raise GrantError("Нет нового пароля")
        # В БД пишем ХЕШ, игроку отдаём plain
        stored = hash_password(new_password)
        col = identifier(cfg.password_col)
        return f"UPDATE {table} SET {col}=%s WHERE {nick}=%s", (stored, order["nickname"])

    if kind == "skin":
        col = identifier(cfg.skin_col)
        return f"UPDATE {table} SET {col}=%s WHERE {nick}=%s", (amount, order["nickname"])

    col_map = {
        "level": cfg.level_col,
        "admin": cfg.admin_col,
        "money": cfg.money_col,
    }
    col_name = col_map.get(kind)
    if not col_name:
        raise GrantError("Неизвестный тип покупки")
    col = identifier(col_name)
    if kind == "admin":
        return f"UPDATE {table} SET {col}=GREATEST({col}, %s) WHERE {nick}=%s", (amount, order["nickname"])
    return f"UPDATE {table} SET {col}={col}+%s WHERE {nick}=%s", (amount, order["nickname"])


def grant_to_game(order: dict, cfg: GameConfig) -> str:
    """Возвращает 'applied' / 'already' или 'pwd:XXXXXXXX' для сброса пароля."""
    cfg.check()
    try:
        import mysql.connector
    except ImportError as exc:
        raise GrantError("Установи mysql-connector-python") from exc

    new_pwd = gen_password(8) if order["kind"] == "password" else None
    connection = None
    try:
        connection = mysql.connector.connect(
            host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
            database=cfg.database, connection_timeout=10, allow_local_infile=False,
            autocommit=False, use_pure=True, auth_plugin="mysql_native_password",
        )
        cur = connection.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS bot_donation_grants (
              order_id BIGINT NOT NULL PRIMARY KEY,
              account_name VARCHAR(64) NOT NULL,
              kind VARCHAR(16) NOT NULL,
              units BIGINT NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        connection.commit()
        connection.start_transaction()

        table, nick = identifier(cfg.table), identifier(cfg.nickname_col)
        cur.execute(f"SELECT {nick} FROM {table} WHERE {nick}=%s FOR UPDATE", (order["nickname"],))
        first = cur.fetchone()
        if first is None:
            raise GrantError(f"Аккаунт {order['nickname']} не найден в игровой базе")
        if cur.fetchone() is not None:
            raise GrantError("Несколько аккаунтов с таким ником — выдача остановлена")

        cur.execute(
            "SELECT order_id FROM bot_donation_grants WHERE order_id=%s FOR UPDATE",
            (order["id"],),
        )
        if cur.fetchone():
            connection.commit()
            return "already"

        query, args = grant_sql(order, cfg, new_pwd)
        cur.execute(query, args)
        if cur.rowcount != 1:
            raise GrantError("Не удалось однозначно изменить аккаунт")

        cur.execute(
            "INSERT INTO bot_donation_grants(order_id,account_name,kind,units) VALUES(%s,%s,%s,%s)",
            (order["id"], order["nickname"], order["kind"], order["units"]),
        )
        connection.commit()
        if new_pwd:
            return f"pwd:{new_pwd}"
        return "applied"
    except GrantError:
        if connection:
            connection.rollback()
        raise
    except Exception as exc:
        if connection:
            connection.rollback()
        raise GrantError(f"Ошибка MySQL: {exc}") from exc
    finally:
        if connection:
            connection.close()


def account_exists(nickname: str, cfg: GameConfig) -> bool:
    cfg.check()
    try:
        import mysql.connector
    except ImportError:
        return False
    try:
        conn = mysql.connector.connect(
            host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
            database=cfg.database, connection_timeout=7, allow_local_infile=False,
            autocommit=True, use_pure=True, auth_plugin="mysql_native_password",
        )
        cur = conn.cursor()
        table, nick = identifier(cfg.table), identifier(cfg.nickname_col)
        cur.execute(f"SELECT 1 FROM {table} WHERE {nick}=%s LIMIT 1", (nickname,))
        ok = cur.fetchone() is not None
        cur.close()
        conn.close()
        return ok
    except Exception:
        logging.exception("account_exists")
        return False


def fetch_player(nickname: str, cfg: GameConfig) -> dict | None:
    cfg.check()
    try:
        import mysql.connector
    except ImportError:
        return None
    try:
        conn = mysql.connector.connect(
            host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
            database=cfg.database, connection_timeout=7, allow_local_infile=False,
            autocommit=True, use_pure=True, auth_plugin="mysql_native_password",
        )
        cur = conn.cursor(dictionary=True)
        table = identifier(cfg.table)
        nick = identifier(cfg.nickname_col)
        cols = ", ".join(
            identifier(c)
            for c in (
                cfg.nickname_col, cfg.level_col, cfg.admin_col,
                cfg.money_col, cfg.skin_col,
            )
        )
        cur.execute(f"SELECT {cols} FROM {table} WHERE {nick}=%s LIMIT 1", (nickname,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except Exception:
        logging.exception("fetch_player")
        return None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class OrderStorage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                nickname TEXT NOT NULL,
                item_code TEXT NOT NULL,
                item_title TEXT NOT NULL,
                kind TEXT NOT NULL,
                units INTEGER NOT NULL,
                price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'awaiting_receipt',
                payment_method TEXT DEFAULT 'stars',
                promo_code TEXT,
                discount INTEGER DEFAULT 0,
                cryptobot_invoice_id TEXT,
                receipt_file_id TEXT,
                receipt_unique_id TEXT,
                receipt_kind TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_text TEXT
            );
            CREATE INDEX IF NOT EXISTS order_owner ON orders (telegram_id, id);
            CREATE INDEX IF NOT EXISTS order_status ON orders (status, id);
            CREATE UNIQUE INDEX IF NOT EXISTS receipt_dedupe
              ON orders (receipt_unique_id) WHERE receipt_unique_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS category_photos (
                category TEXT PRIMARY KEY, photo_file_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS moderators (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                percent INTEGER NOT NULL,
                max_uses INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_prices (
                product_id TEXT PRIMARY KEY,
                price INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_id INTEGER PRIMARY KEY,
                game_nickname TEXT,
                registered INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(orders)").fetchall()}
        for col, typ in [
            ("payment_method", "TEXT DEFAULT 'stars'"),
            ("promo_code", "TEXT"),
            ("discount", "INTEGER DEFAULT 0"),
            ("cryptobot_invoice_id", "TEXT"),
        ]:
            if col not in cols:
                self.db.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
        pcols = {r[1] for r in self.db.execute("PRAGMA table_info(user_profiles)").fetchall()}
        if "registered" not in pcols:
            self.db.execute("ALTER TABLE user_profiles ADD COLUMN registered INTEGER DEFAULT 0")
        self.db.commit()
        for private_path in (path, path + "-wal", path + "-shm"):
            if os.path.exists(private_path):
                try:
                    os.chmod(private_path, 0o600)
                except OSError:
                    pass

    def setting(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else str(default)

    def set_settings(self, values: dict[str, str]) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO app_settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(k, str(v)) for k, v in values.items()],
            )

    def photo(self, category: str) -> str | None:
        row = self.db.execute(
            "SELECT photo_file_id FROM category_photos WHERE category=?", (category,)
        ).fetchone()
        return row["photo_file_id"] if row else None

    def set_photo(self, category: str, file_id: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO category_photos(category,photo_file_id) VALUES(?,?) "
                "ON CONFLICT(category) DO UPDATE SET photo_file_id=excluded.photo_file_id",
                (category, file_id),
            )

    def remove_photo(self, category: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM category_photos WHERE category=?", (category,))

    def get_price(self, product_id: str) -> float:
        """Цена товара в USD (Stars считаются отдельно: USD * STARS_PER_USD)."""
        row = self.db.execute(
            "SELECT price FROM product_prices WHERE product_id=?", (product_id,)
        ).fetchone()
        if row:
            return float(row["price"])
        return float(PRODUCTS[product_id]["price"])

    def set_price(self, product_id: str, price: float) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO product_prices(product_id,price) VALUES(?,?) "
                "ON CONFLICT(product_id) DO UPDATE SET price=excluded.price",
                (product_id, float(price)),
            )

    def product(self, code: str) -> dict | None:
        if code not in PRODUCTS:
            return None
        p = dict(PRODUCTS[code])
        p["price"] = self.get_price(code)
        return p

    def is_moderator(self, uid: int) -> bool:
        if uid == OWNER_ID:
            return True
        row = self.db.execute(
            "SELECT 1 FROM moderators WHERE telegram_id=?", (uid,)
        ).fetchone()
        return bool(row)

    def add_moderator(self, uid: int, username: str = "") -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO moderators(telegram_id,username,added_at) VALUES(?,?,?)",
                (uid, username or "", now()),
            )

    def remove_moderator(self, uid: int) -> bool:
        cur = self.db.execute("DELETE FROM moderators WHERE telegram_id=?", (uid,))
        self.db.commit()
        return cur.rowcount > 0

    def list_moderators(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM moderators ORDER BY added_at")]

    def add_promo(self, code: str, percent: int, max_uses: int = 0) -> None:
        code = code.upper().strip()
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO promocodes(code,percent,max_uses,used_count,active,created_at) "
                "VALUES(?,?,?,0,1,?)",
                (code, percent, max_uses, now()),
            )

    def get_promo(self, code: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM promocodes WHERE code=? AND active=1", (code.upper().strip(),)
        ).fetchone()
        return dict(row) if row else None

    def use_promo(self, code: str) -> bool:
        promo = self.get_promo(code)
        if not promo:
            return False
        if promo["max_uses"] > 0 and promo["used_count"] >= promo["max_uses"]:
            return False
        with self.db:
            self.db.execute(
                "UPDATE promocodes SET used_count=used_count+1 WHERE code=?",
                (code.upper().strip(),),
            )
        return True

    def list_promos(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM promocodes ORDER BY created_at DESC")]

    def deactivate_promo(self, code: str) -> None:
        with self.db:
            self.db.execute("UPDATE promocodes SET active=0 WHERE code=?", (code.upper().strip(),))

    def set_nickname(self, uid: int, nick: str, registered: bool = True) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO user_profiles(telegram_id,game_nickname,registered,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET "
                "game_nickname=excluded.game_nickname, registered=excluded.registered, updated_at=excluded.updated_at",
                (uid, nick, 1 if registered else 0, now()),
            )

    def clear_nickname(self, uid: int) -> None:
        """Выход из игрового аккаунта — можно привязать другой ник."""
        with self.db:
            self.db.execute(
                "UPDATE user_profiles SET game_nickname=NULL, registered=0, updated_at=? WHERE telegram_id=?",
                (now(), uid),
            )

    def get_nickname(self, uid: int) -> str | None:
        row = self.db.execute(
            "SELECT game_nickname FROM user_profiles WHERE telegram_id=?", (uid,)
        ).fetchone()
        return row["game_nickname"] if row and row["game_nickname"] else None

    def is_registered(self, uid: int) -> bool:
        row = self.db.execute(
            "SELECT registered, game_nickname FROM user_profiles WHERE telegram_id=?", (uid,)
        ).fetchone()
        return bool(row and row["registered"] and row["game_nickname"])

    @staticmethod
    def _row(cur):
        row = cur.fetchone()
        return dict(row) if row else None

    def get(self, order_id: int):
        return self._row(self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)))

    def active(self, user_id: int):
        return self._row(
            self.db.execute(
                "SELECT * FROM orders WHERE telegram_id=? AND status IN "
                "('awaiting_receipt','awaiting_stars','review','processing','awaiting_crypto') "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
        )

    def latest(self, user_id: int, limit=10):
        return [
            dict(x)
            for x in self.db.execute(
                "SELECT * FROM orders WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
        ]

    def pending(self):
        return [dict(x) for x in self.db.execute(
            "SELECT * FROM orders WHERE status='review' ORDER BY id ASC"
        )]

    def create(
        self,
        telegram_id: int,
        nickname: str,
        item_code: str,
        item: dict,
        payment_method: str = "stars",
        promo_code: str | None = None,
        discount: int = 0,
        final_price: int | None = None,
        units_override: int | None = None,
    ):
        """final_price — цена в USD (целое или дробное округляется до 2 знаков → cents/100).
        В БД price хранится как целое число Stars к оплате (USD * STARS_PER_USD).
        """
        if self.active(telegram_id):
            raise ValueError("Есть незавершённый заказ")
        t = now()
        usd = float(final_price if final_price is not None else item["price"])
        stars = usd_to_stars(usd)
        units = int(units_override if units_override is not None else item["units"])
        status = "awaiting_crypto" if payment_method == "crypto" else "awaiting_stars"
        title = item["title"]
        if item["kind"] == "money" and units_override:
            title = f"{units:,} игровой валюты".replace(",", " ")
        cur = self.db.execute(
            "INSERT INTO orders (telegram_id,nickname,item_code,item_title,kind,units,price,"
            "status,payment_method,promo_code,discount,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                telegram_id, nickname, item_code, title, item["kind"],
                units, stars, status, payment_method, promo_code,
                discount, t, t,
            ),
        )
        self.db.commit()
        order = self.get(cur.lastrowid)
        if order:
            order["usd"] = usd
            order["stars"] = stars
        return order

    def set_crypto_invoice(self, order_id: int, invoice_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE orders SET cryptobot_invoice_id=?, updated_at=? WHERE id=?",
                (invoice_id, now(), order_id),
            )

    def submit_receipt(self, order_id: int, user_id: int, file_id: str, unique_id: str, kind: str):
        try:
            cur = self.db.execute(
                """UPDATE orders SET receipt_file_id=?,receipt_unique_id=?,receipt_kind=?,
                 status='review',updated_at=? WHERE id=? AND telegram_id=? AND status='awaiting_receipt'""",
                (file_id, unique_id, kind, now(), order_id, user_id),
            )
            self.db.commit()
            return cur.rowcount == 1
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise ValueError("Этот чек уже используется в другом заказе") from exc

    def cancel(self, order_id: int, user_id: int):
        cur = self.db.execute(
            "UPDATE orders SET status='cancelled',updated_at=? WHERE id=? AND telegram_id=? "
            "AND status IN ('awaiting_receipt','awaiting_crypto','awaiting_stars','review')",
            (now(), order_id, user_id),
        )
        self.db.commit()
        return cur.rowcount == 1

    def claim(self, order_id: int):
        cur = self.db.execute(
            "UPDATE orders SET status='processing',updated_at=?,error_text=NULL "
            "WHERE id=? AND status='review'",
            (now(), order_id),
        )
        self.db.commit()
        return cur.rowcount == 1

    def delivered(self, order_id: int):
        self.db.execute(
            "UPDATE orders SET status='delivered',updated_at=?,error_text=NULL WHERE id=?",
            (now(), order_id),
        )
        self.db.commit()

    def reject(self, order_id: int):
        cur = self.db.execute(
            "UPDATE orders SET status='rejected',updated_at=? WHERE id=? AND status='review'",
            (now(), order_id),
        )
        self.db.commit()
        return cur.rowcount == 1

    def retry(self, order_id: int, error: str):
        self.db.execute(
            "UPDATE orders SET status='review',error_text=?,updated_at=? WHERE id=?",
            (error[:500], now(), order_id),
        )
        self.db.commit()

    def mark_paid_crypto(self, order_id: int):
        cur = self.db.execute(
            "UPDATE orders SET status='processing',updated_at=? "
            "WHERE id=? AND status='awaiting_crypto'",
            (now(), order_id),
        )
        self.db.commit()
        return cur.rowcount == 1

    def recover_stuck(self):
        self.db.execute(
            "UPDATE orders SET status='review',updated_at=? WHERE status='processing'",
            (now(),),
        )
        self.db.commit()

    def stats(self) -> dict:
        rows = self.db.execute(
            "SELECT status, COUNT(*) c, COALESCE(SUM(price),0) s FROM orders GROUP BY status"
        ).fetchall()
        data = {r["status"]: {"count": r["c"], "sum": r["s"]} for r in rows}
        delivered = data.get("delivered", {"count": 0, "sum": 0})
        return {
            "total_orders": sum(v["count"] for v in data.values()),
            "delivered_count": delivered["count"],
            "revenue": delivered["sum"],
            "by_status": data,
        }


# ─── UI ──────────────────────────────────────────────────────────────────────
EMOJIS = {
    "star": ("5260297215540251496", "⭐"),
    "lock": ("5260400842683349625", "🛡"),
    "money": ("5260400842683349625", "💰"),
    "check": ("5260400842683349625", "✅"),
    "warning": ("5260400842683349625", "⚠️"),
    "home": ("5260400842683349625", "🏠"),
    "card": ("5260400842683349625", "🧾"),
    "paperclip": ("5260400842683349625", "📎"),
    "settings": ("5260400842683349625", "⚙️"),
    "user": ("5260400842683349625", "👤"),
    "promo": ("5260400842683349625", "🎟"),
    "crypto": ("5260400842683349625", "🪙"),
    "stars": ("5260400842683349625", "✨"),
    "key": ("5260400842683349625", "🔑"),
    "skin": ("5260400842683349625", "🧍"),
}


def em(key: str) -> str:
    eid, symbol = EMOJIS.get(key, ("", "•"))
    return f'<tg-emoji emoji-id="{eid}">{symbol}</tg-emoji>' if PREMIUM else symbol


def button(label: str, data: str, emoji: str | None = None, style: str | None = None):
    kw = {"text": label, "callback_data": data}
    if PREMIUM and emoji in EMOJIS:
        kw["icon_custom_emoji_id"] = EMOJIS[emoji][0]
    if style:
        kw["style"] = style
    return InlineKeyboardButton(**kw)


def keyboard(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def home_kb(owner: bool = False, is_mod: bool = False):
    rows = []
    if WEBAPP_URL:
        rows.append((
            InlineKeyboardButton(
                text="🛒 WAIP SHOP",
                web_app=WebAppInfo(url=WEBAPP_URL),
            ),
        ))
    if APK_DOWNLOAD_URL:
        rows.append((
            InlineKeyboardButton(text=APK_BUTTON_TITLE, url=APK_DOWNLOAD_URL),
        ))
    rows.extend([
        (button("⭐ ЛВЛ", "cat:level", "star"), button("🛡 Админка", "cat:admin", "lock")),
        (button("🧍 Скины", "cat:skin", "skin"), button("💰 Валюта", "cat:money", "money")),
        (button("🔑 Сброс пароля", "cat:password", "key"),),
        (button("👤 Профиль", "profile", "user"), button("📦 Мои покупки", "my", "paperclip")),
        (button("🎟 Промокод", "promo_enter", "promo"),),
    ])
    if owner or is_mod:
        rows.append((button("⚙️ Панель", "adm:home", "settings"),))
    return keyboard(*rows)


def section_kb(category: str):
    rows = []
    for p in CATALOG.get(category, {}).get("products") or []:
        price = store.get_price(p["id"])
        stars = usd_to_stars(price)
        rows.append((button(f"{p['title']} — ${price} / {stars}★", f"buy:{p['id']}", "card"),))
    if category == "money":
        rows.append((button("Своя сумма — в Web App", "home", "money"),))
    rows.append((button("Назад", "home", "home"),))
    return keyboard(*rows)


def payment_kb(order_id: int):
    return keyboard(
        (button("❌ Отменить заказ", f"cancel:{order_id}", "warning", "danger"),),
        (button("🏠 Меню", "home", "home"),),
    )


def pay_method_kb(code: str):
    rows = [(button("✨ Telegram Stars (подарок)", f"pay:stars:{code}", "stars"),)]
    if CRYPTOBOT_TOKEN:
        rows.append((button("🪙 CryptoBot (авто)", f"pay:crypto:{code}", "crypto"),))
    rows.append((button("⬅️ Назад", "home", "home"),))
    return keyboard(*rows)


def review_kb(order_id: int):
    return keyboard(
        (button("✅ Подтвердить и выдать", f"ok:{order_id}", "check", "success"),),
        (button("❌ Отклонить чек", f"no:{order_id}", "warning", "danger"),),
    )


def admin_panel_kb(is_owner: bool = True):
    rows = [
        (button("📋 Чеки на проверке", "adm:orders", "card"),),
        (button("📊 Статистика", "adm:stats", "card"),),
    ]
    if is_owner:
        rows.extend([
            (button("💰 Изменить цены", "adm:prices", "money"),),
            (button("🎟 Промокоды", "adm:promos", "promo"),),
            (button("👥 Модераторы", "adm:mods", "user"),),
            (button("🗄 MySQL / Поля", "adm:db", "settings"),),
            (button("🖼 Фото категорий", "adm:photos", "card"),),
        ])
    rows.append((button("🏠 В меню", "home", "home"),))
    return keyboard(*rows)


def subscribe_kb():
    rows = []
    if CHANNEL_USERNAME:
        rows.append((InlineKeyboardButton(
            text="📢 Подписаться на канал",
            url=f"https://t.me/{CHANNEL_USERNAME}",
        ),))
    rows.append((button("✅ Я подписался", "check_sub", "check"),))
    return keyboard(*rows)


# ─── CryptoBot ───────────────────────────────────────────────────────────────
def cryptobot_request(method: str, params: dict | None = None) -> dict:
    if not CRYPTOBOT_TOKEN:
        raise GrantError("CRYPTOBOT_TOKEN не задан")
    url = f"https://pay.crypt.bot/api/{method}"
    data = json.dumps(params or {}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        raise GrantError(body.get("error", {}).get("name", str(e))) from e
    if not body.get("ok"):
        raise GrantError(str(body.get("error", body)))
    return body["result"]


def create_crypto_invoice(order: dict) -> dict:
    # price в заказе = Stars; переводим в USD для CryptoBot
    stars = int(order["price"])
    usd = max(0.01, round(stars / float(STARS_PER_USD), 2))
    return cryptobot_request("createInvoice", {
        "currency_type": "fiat",
        "fiat": "USD",
        "amount": str(usd),
        "description": f"WAIP SHOP #{order['id']} — {order['item_title']}",
        "payload": str(order["id"]),
        "expires_in": 3600,
        "allow_comments": False,
        "allow_anonymous": False,
    })


async def create_stars_invoice_link_with_bot(bot: Bot, order: dict) -> str:
    """Ссылка на оплату Telegram Stars (XTR) — средства на баланс бота."""
    stars = max(1, int(order["price"]))
    title = (order.get("item_title") or "WAIP SHOP")[:32]
    description = f"Заказ #{order['id']} · {order.get('nickname') or ''}"[:255]
    prices = [LabeledPrice(label=title[:32], amount=stars)]
    link = await bot.create_invoice_link(
        title=title,
        description=description,
        payload=f"waip:{order['id']}",
        currency="XTR",
        prices=prices,
    )
    return link


async def fulfill_paid_order(bot: Bot, order_id: int, source: str = "pay") -> None:
    """Авто-выдача после успешной оплаты (Stars XTR / CryptoBot / gift auto)."""
    order = store.get(order_id)
    if not order:
        return
    if order["status"] in ("delivered", "done"):
        return
    with store.db:
        store.db.execute(
            "UPDATE orders SET status='processing',updated_at=? WHERE id=?",
            (now(), order_id),
        )
    order = store.get(order_id)
    try:
        result = await asyncio.to_thread(grant_to_game, order, game_config())
        store.delivered(order_id)
        if isinstance(result, str) and result.startswith("pwd:"):
            await _notify_password(bot, order, result[4:])
        else:
            try:
                await bot.send_message(
                    order["telegram_id"],
                    f"<b>Оплата получена</b>\n"
                    f"Заказ #{order_id} — <b>{escape(order['item_title'])}</b>\n"
                    f"Ник: <code>{escape(order['nickname'])}</code>\n"
                    f"Товар выдан автоматически.",
                )
            except Exception:
                logging.exception("notify buyer")
        try:
            await bot.send_message(
                OWNER_ID,
                f"Оплата ({source}) #{order_id} → {escape(order['nickname'])} · {escape(order['item_title'])}",
            )
        except Exception:
            pass
    except GrantError as e:
        store.retry(order_id, str(e))
        try:
            await bot.send_message(
                OWNER_ID,
                f"Оплачен, но выдача упала #{order_id}:\n{escape(str(e))}",
            )
        except Exception:
            pass
        logging.exception("fulfill grant %s", order_id)


@dp.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    # Всегда подтверждаем — проверка заказа при successful_payment
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot):
    sp: SuccessfulPayment = message.successful_payment
    payload = sp.invoice_payload or ""
    if not payload.startswith("waip:"):
        return
    try:
        order_id = int(payload.split(":", 1)[1])
    except ValueError:
        return
    order = store.get(order_id)
    if not order:
        return
    if order["telegram_id"] != message.from_user.id:
        logging.warning("payment user mismatch order=%s", order_id)
        return
    await fulfill_paid_order(bot, order_id, source="stars-xtr")


def check_crypto_invoice(invoice_id: str) -> str:
    result = cryptobot_request("getInvoices", {"invoice_ids": str(invoice_id)})
    items = result if isinstance(result, list) else result.get("items", [])
    if not items:
        return "unknown"
    return items[0].get("status", "unknown")


# ─── Game config ─────────────────────────────────────────────────────────────
def game_config() -> GameConfig:
    return GameConfig(
        host=store.setting("mysql_host", C.GAME_DB_HOST),
        port=int(store.setting("mysql_port", C.GAME_DB_PORT)),
        user=store.setting("mysql_user", C.GAME_DB_USER),
        password=store.setting("mysql_password", C.GAME_DB_PASSWORD),
        database=store.setting("mysql_database", C.GAME_DB_NAME),
        table=store.setting("mysql_table", C.GAME_ACCOUNT_TABLE),
        nickname_col=store.setting("mysql_nickname_col", C.GAME_NICK_COLUMN),
        level_col=store.setting("mysql_level_col", C.GAME_LEVEL_COLUMN),
        admin_col=store.setting("mysql_admin_col", C.GAME_ADMIN_COLUMN),
        money_col=store.setting("mysql_money_col", C.GAME_MONEY_COLUMN),
        skin_col=store.setting("mysql_skin_col", getattr(C, "GAME_SKIN_COLUMN", "skin")),
        password_col=store.setting("mysql_password_col", getattr(C, "GAME_PASSWORD_COLUMN", "password")),
    )


def mysql_probe(cfg: GameConfig, check_schema: bool = False) -> str:
    if not cfg.host or not cfg.user or not cfg.database:
        raise GrantError("Укажи хост, логин и имя базы")
    try:
        import mysql.connector
    except ImportError as exc:
        raise GrantError("Нужна mysql-connector-python") from exc
    try:
        connection = mysql.connector.connect(
            host=cfg.host, port=cfg.port, user=cfg.user,
            password=cfg.password, database=cfg.database,
            allow_local_infile=False, connection_timeout=7, autocommit=True,
        )
        try:
            cur = connection.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            if check_schema:
                for x in (cfg.table, cfg.nickname_col, cfg.level_col, cfg.admin_col,
                          cfg.money_col, cfg.skin_col, cfg.password_col):
                    identifier(x)
                cur.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (cfg.database, cfg.table),
                )
                found = {row[0].lower() for row in cur.fetchall()}
                if not found:
                    raise GrantError(f"Нет таблицы {cfg.table}")
                wanted = (cfg.nickname_col, cfg.level_col, cfg.admin_col,
                          cfg.money_col, cfg.skin_col, cfg.password_col)
                missing = [v for v in wanted if v.lower() not in found]
                if missing:
                    raise GrantError("Нет столбцов: " + ", ".join(missing))
            cur.close()
        finally:
            connection.close()
    except GrantError:
        raise
    except Exception as exc:
        raise GrantError(f"MySQL: {exc}") from exc
    return "ok" if check_schema else "connected"


store = OrderStorage(DB_PATH)
store.recover_stuck()
dp = Dispatcher()

waiting_for_nickname: dict[int, str] = {}   # product buy flow
waiting_for_promo: dict[int, bool] = {}
waiting_pay: dict[int, dict] = {}
waiting_register: set[int] = set()          # onboarding: enter nick
admin_input: dict[int, dict] = {}


def owner_only(uid: int, chat_id: int) -> bool:
    return uid == OWNER_ID and chat_id == OWNER_ID


def can_review(uid: int) -> bool:
    return store.is_moderator(uid)


def channel_ref():
    if CHANNEL_ID:
        return CHANNEL_ID
    if CHANNEL_USERNAME:
        return f"@{CHANNEL_USERNAME}"
    return None


async def is_subscribed(bot: Bot, user_id: int) -> bool:
    ref = channel_ref()
    if not ref:
        return True  # проверка отключена
    try:
        member = await bot.get_chat_member(ref, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        )
    except Exception:
        logging.exception("is_subscribed")
        return False


def order_description(o: dict) -> str:
    pay = {"stars": "✨ Stars", "crypto": "🪙 CryptoBot"}.get(
        o.get("payment_method") or "stars", "?"
    )
    promo = ""
    if o.get("promo_code"):
        promo = f"\nПромокод: <code>{escape(o['promo_code'])}</code> (−{o['discount']}%)"
    return (
        f"{em('card')} <b>Заказ #{o['id']}</b>\n"
        f"Товар: <b>{escape(o['item_title'])}</b>\n"
        f"Ник: <code>{escape(o['nickname'])}</code>\n"
        f"TG ID: <code>{o['telegram_id']}</code>\n"
        f"Сумма: <b>{o['price']} ₽</b>{promo}\n"
        f"Оплата: {pay}\n"
        f"Статус: <code>{escape(o['status'])}</code>"
    )


async def home(message: Message):
    is_owner = message.from_user.id == OWNER_ID
    is_mod = store.is_moderator(message.from_user.id)
    await message.answer(
        f"<b>🛒 WAIP SHOP</b>\n\n"
        f"Магазин привилегий сервера.\n"
        f"Нажми <b>🛒 WAIP SHOP</b> для удобного приложения\n"
        f"или выбери категорию ниже.",
        reply_markup=home_kb(is_owner, is_mod),
    )


async def start_onboarding(message: Message, bot: Bot):
    uid = message.from_user.id
    # Если WebApp настроен — ведём пользователя туда (подписка + ник + магазин внутри)
    if WEBAPP_URL:
        rows = [[InlineKeyboardButton(text="🛒 Открыть WAIP SHOP", web_app=WebAppInfo(url=WEBAPP_URL))]]
        if APK_DOWNLOAD_URL:
            rows.append([InlineKeyboardButton(text=APK_BUTTON_TITLE, url=APK_DOWNLOAD_URL)])
        await message.answer(
            f"<b>🛒 WAIP SHOP</b>\n\n"
            f"✨ Нажми кнопку ниже — откроется приложение.\n"
            f"Там проверка подписки, ник и весь магазин с анимациями.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return
    if not await is_subscribed(bot, uid):
        ch = f"@{CHANNEL_USERNAME}" if CHANNEL_USERNAME else "наш канал"
        await message.answer(
            f"<b>Добро пожаловать в WAIP SHOP!</b>\n\n"
            f"Чтобы пользоваться ботом, подпишись на {ch} и нажми кнопку ниже.",
            reply_markup=subscribe_kb(),
        )
        return
    if not store.is_registered(uid):
        waiting_register.add(uid)
        await message.answer(
            f"{em('user')} <b>Регистрация</b>\n\n"
            f"Отправь <b>игровой ник</b> своего аккаунта на сервере "
            f"(точно как в игре, например <code>Ivan_Ivanov</code>).\n\n"
            f"Бот проверит, что такой аккаунт существует.",
        )
        return
    await home(message)


# ─── Commands ────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def start(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    await start_onboarding(message, bot)


@dp.message(Command("cancel"))
async def cancel_cmd(message: Message):
    uid = message.from_user.id
    waiting_for_nickname.pop(uid, None)
    waiting_for_promo.pop(uid, None)
    waiting_pay.pop(uid, None)
    waiting_register.discard(uid)
    admin_input.pop(uid, None)
    order = store.active(uid)
    if order and order["status"] in ("awaiting_receipt", "awaiting_crypto"):
        store.cancel(order["id"], uid)
        await message.answer(f"{em('check')} Заказ #{order['id']} отменён.", reply_markup=home_kb())
    else:
        await message.answer("Ок, отменено.", reply_markup=home_kb() if store.is_registered(uid) else None)


@dp.message(Command("myorders"))
async def myorders_cmd(message: Message):
    if not store.is_registered(message.from_user.id):
        await message.answer("Сначала пройди регистрацию: /start")
        return
    await show_orders(message, message.from_user.id)


@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not can_review(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await show_admin(message)


async def show_orders(message: Message, uid: int):
    orders = store.latest(uid)
    if not orders:
        await message.answer("Покупок пока нет.", reply_markup=home_kb())
        return
    lines = [f"{em('paperclip')} <b>Твои заказы</b>\n"]
    kb_rows = []
    for o in orders[:8]:
        st = {
            "delivered": "✅", "rejected": "❌", "cancelled": "🚫",
            "review": "⏳", "awaiting_receipt": "📷", "awaiting_crypto": "🪙",
            "processing": "⚙️",
        }.get(o["status"], "•")
        lines.append(f"{st} <b>#{o['id']}</b> {escape(o['item_title'])} — {o['price']} ₽")
        if o["status"] == "delivered":
            kb_rows.append((button(f"🔁 Повторить #{o['id']}", f"repeat:{o['id']}", "check"),))
    kb_rows.append((button("🏠 Меню", "home", "home"),))
    await message.answer("\n".join(lines), reply_markup=keyboard(*kb_rows))


async def show_admin(message: Message):
    is_owner = message.from_user.id == OWNER_ID
    await message.answer(
        f"{em('settings')} <b>Панель WAIP SHOP</b>\n"
        f"{'Владелец' if is_owner else 'Модератор'}",
        reply_markup=admin_panel_kb(is_owner),
    )


async def show_admin_orders(message: Message, bot: Bot):
    orders = store.pending()
    if not orders:
        await message.answer(
            "Новых чеков нет.",
            reply_markup=admin_panel_kb(message.from_user.id == OWNER_ID),
        )
        return
    await message.answer(f"{em('card')} <b>Чеки на проверке: {len(orders)}</b>")
    for order in orders:
        try:
            caption = order_description(order)
            markup = review_kb(order["id"])
            if order.get("receipt_kind") == "photo":
                await bot.send_photo(message.chat.id, order["receipt_file_id"], caption=caption, reply_markup=markup)
            elif order.get("receipt_kind") == "document":
                await bot.send_document(message.chat.id, order["receipt_file_id"], caption=caption, reply_markup=markup)
            else:
                await message.answer(caption, reply_markup=markup)
        except Exception:
            logging.exception("resend receipt %s", order["id"])
            await message.answer(order_description(order), reply_markup=review_kb(order["id"]))


# ─── Callbacks ───────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "check_sub")
async def callback_check_sub(call: CallbackQuery, bot: Bot):
    await call.answer()
    if not await is_subscribed(bot, call.from_user.id):
        await call.message.answer(
            f"{em('warning')} Подписка не найдена. Подпишись и нажми снова.",
            reply_markup=subscribe_kb(),
        )
        return
    if store.is_registered(call.from_user.id):
        await home(call.message)
        return
    waiting_register.add(call.from_user.id)
    await call.message.answer(
        f"{em('check')} Подписка есть!\n\n"
        f"{em('user')} <b>Регистрация</b>\n"
        f"Отправь игровой ник аккаунта (как в игре):",
    )


@dp.callback_query(F.data == "home")
async def callback_home(call: CallbackQuery, bot: Bot):
    await call.answer()
    if not store.is_registered(call.from_user.id):
        await start_onboarding(call.message, bot)
        return
    await home(call.message)


@dp.callback_query(F.data == "my")
async def callback_my(call: CallbackQuery):
    await call.answer()
    if not store.is_registered(call.from_user.id):
        await call.message.answer("Сначала /start")
        return
    await show_orders(call.message, call.from_user.id)


@dp.callback_query(F.data == "profile")
async def callback_profile(call: CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    if not store.is_registered(uid):
        await call.message.answer("Сначала /start")
        return
    nick = store.get_nickname(uid)
    tg_user = call.from_user.username or "—"
    text = (
        f"{em('user')} <b>Профиль</b>\n\n"
        f"Telegram: @{escape(tg_user)}\n"
        f"ID: <code>{uid}</code>\n"
        f"Игровой ник: <code>{escape(nick or '—')}</code>\n\n"
    )
    if nick:
        player = await asyncio.to_thread(fetch_player, nick, game_config())
        if player:
            cfg = game_config()
            def g(col):
                return player.get(col) or player.get(col.lower()) or "?"
            text += (
                f"<b>Данные с сервера (live):</b>\n"
                f"⭐ Уровень: <b>{g(cfg.level_col)}</b>\n"
                f"🛡 Админка: <b>{g(cfg.admin_col)}</b>\n"
                f"🧍 Скин: <b>{g(cfg.skin_col)}</b>\n"
                f"💰 Валюта: <b>{g(cfg.money_col)}</b>\n"
            )
        else:
            text += "Не удалось получить данные с сервера."
    await call.message.answer(text, reply_markup=home_kb())


@dp.callback_query(F.data == "promo_enter")
async def callback_promo_enter(call: CallbackQuery):
    await call.answer()
    if not store.is_registered(call.from_user.id):
        await call.message.answer("Сначала /start")
        return
    waiting_for_promo[call.from_user.id] = True
    await call.message.answer(
        f"{em('promo')} Отправь промокод.\nОн применится к следующему заказу.\n/cancel — отмена."
    )


@dp.callback_query(F.data.startswith("cat:"))
async def callback_category(call: CallbackQuery):
    if not store.is_registered(call.from_user.id):
        await call.answer("Сначала регистрация /start", show_alert=True)
        return
    category = call.data.partition(":")[2]
    if category not in CATALOG:
        await call.answer("Категория не найдена", show_alert=True)
        return
    await call.answer()
    caption = f"<b>{escape(CATALOG[category]['title'])}</b>\n\nВыбери:"
    photo_id = store.photo(category)
    if photo_id:
        try:
            await call.message.answer_photo(photo_id, caption=caption, reply_markup=section_kb(category))
            return
        except Exception:
            pass
    await call.message.answer(caption, reply_markup=section_kb(category))


@dp.callback_query(F.data.startswith("buy:"))
async def callback_buy(call: CallbackQuery):
    if not store.is_registered(call.from_user.id):
        await call.answer("Сначала /start", show_alert=True)
        return
    code = call.data.partition(":")[2]
    item = store.product(code)
    if not item:
        await call.answer("Товар не найден", show_alert=True)
        return
    if store.active(call.from_user.id):
        await call.answer("Уже есть активный заказ", show_alert=True)
        return

    nick = store.get_nickname(call.from_user.id)
    if not nick:
        waiting_for_nickname[call.from_user.id] = code
        await call.answer()
        await call.message.answer(
            f"{em('check')} <b>{escape(item['title'])}</b> — {item['price']} ₽\n\n"
            "Отправь игровой ник:"
        )
        return

    # ник уже есть — сразу к оплате
    promo = (waiting_pay.get(call.from_user.id) or {}).get("promo")
    waiting_pay[call.from_user.id] = {"code": code, "nick": nick, "promo": promo}
    await call.answer()
    await call.message.answer(
        f"{em('check')} <b>{escape(item['title'])}</b> — {item['price']} ₽\n"
        f"Ник: <code>{escape(nick)}</code>\n\n"
        "Выбери способ оплаты:",
        reply_markup=pay_method_kb(code),
    )


@dp.callback_query(F.data.startswith("pay:"))
async def callback_pay_method(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer("Ошибка", show_alert=True)
        return
    method, code = parts[1], parts[2]
    data = waiting_pay.get(call.from_user.id)
    if not data or data.get("code") != code:
        await call.answer("Сессия устарела, начни заново", show_alert=True)
        return
    item = store.product(code)
    if not item:
        await call.answer("Товар не найден", show_alert=True)
        return

    promo_code = data.get("promo")
    discount = 0
    final_price = item["price"]
    if promo_code:
        promo = store.get_promo(promo_code)
        if promo and (promo["max_uses"] == 0 or promo["used_count"] < promo["max_uses"]):
            discount = int(promo["percent"])
            final_price = max(1, int(item["price"] * (100 - discount) / 100))

    try:
        order = store.create(
            call.from_user.id, data["nick"], code, item,
            payment_method=method, promo_code=promo_code,
            discount=discount, final_price=final_price,
        )
        if promo_code:
            store.use_promo(promo_code)
        store.set_nickname(call.from_user.id, data["nick"])
    except ValueError as e:
        await call.answer(str(e), show_alert=True)
        return
    finally:
        waiting_pay.pop(call.from_user.id, None)

    await call.answer()

    if method == "crypto":
        if not CRYPTOBOT_TOKEN:
            store.cancel(order["id"], call.from_user.id)
            await call.message.answer("CryptoBot не настроен.")
            return
        try:
            inv = await asyncio.to_thread(create_crypto_invoice, order)
            invoice_id = str(inv.get("invoice_id") or inv.get("invoiceId"))
            pay_url = inv.get("bot_invoice_url") or inv.get("pay_url") or inv.get("payUrl")
            store.set_crypto_invoice(order["id"], invoice_id)
            await call.message.answer(
                f"{em('crypto')} <b>Заказ #{order['id']}</b>\n"
                f"{escape(item['title'])} — <b>{final_price} ₽</b>\n\n"
                f"Оплати: {pay_url}\n\n"
                f"После оплаты товар выдастся автоматически (до 1 мин).",
                reply_markup=payment_kb(order["id"]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logging.exception("crypto invoice")
            store.cancel(order["id"], call.from_user.id)
            await call.message.answer(f"Ошибка CryptoBot: {escape(str(e))}")
        return

    extra = ""
    if item["kind"] == "password":
        extra = (
            "\n\n⚠️ После оплаты и подтверждения бот сгенерирует <b>новый пароль</b> "
            "и пришлёт его тебе. Сразу смени его в игре."
        )
    if item["kind"] == "skin":
        extra = f"\n\nСкин ID: <code>{item['units']}</code>"

    await call.message.answer(
        f"{em('stars')} <b>Заказ #{order['id']}</b>\n"
        f"{escape(item['title'])} — <b>{final_price} ₽</b>{extra}\n\n"
        f"<b>Оплата Telegram Stars:</b>\n"
        f"1. Открой <b>@{STARS_USER}</b>\n"
        f"2. Отправь <b>подарок (Gift)</b> на сумму ≈ <b>{final_price} ₽</b>\n"
        f"3. Скриншот отправки пришли <b>сюда</b>\n\n"
        f"Чек проверит админ/модератор → товар выдастся автоматически.",
        reply_markup=payment_kb(order["id"]),
    )


@dp.callback_query(F.data.startswith("cancel:"))
async def callback_cancel(call: CallbackQuery):
    try:
        order_id = int(call.data.partition(":")[2])
    except ValueError:
        await call.answer("Ошибка", show_alert=True)
        return
    if store.cancel(order_id, call.from_user.id):
        await call.message.answer(f"{em('check')} Заказ #{order_id} отменён.", reply_markup=home_kb())
        await call.answer("Отменено")
    else:
        await call.answer("Уже нельзя отменить", show_alert=True)


@dp.callback_query(F.data.startswith("repeat:"))
async def callback_repeat(call: CallbackQuery):
    try:
        order_id = int(call.data.partition(":")[2])
    except ValueError:
        await call.answer("Ошибка", show_alert=True)
        return
    old = store.get(order_id)
    if not old or old["telegram_id"] != call.from_user.id or old["status"] != "delivered":
        await call.answer("Нельзя повторить", show_alert=True)
        return
    code = old["item_code"]
    item = store.product(code)
    if not item:
        await call.answer("Товар недоступен", show_alert=True)
        return
    if store.active(call.from_user.id):
        await call.answer("Сначала заверши текущий заказ", show_alert=True)
        return
    waiting_pay[call.from_user.id] = {"code": code, "nick": old["nickname"], "promo": None}
    await call.answer()
    await call.message.answer(
        f"🔁 <b>{escape(item['title'])}</b> — {item['price']} ₽\n"
        f"Ник: <code>{escape(old['nickname'])}</code>\n\nВыбери оплату:",
        reply_markup=pay_method_kb(code),
    )


# ─── Admin ───────────────────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("adm:"))
async def admin_callbacks(call: CallbackQuery, bot: Bot):
    uid = call.from_user.id
    if not can_review(uid):
        await call.answer("Нет доступа", show_alert=True)
        return
    is_owner = uid == OWNER_ID
    action = call.data.partition(":")[2]
    await call.answer()

    if action == "home":
        await show_admin(call.message)
        return
    if action == "orders":
        await show_admin_orders(call.message, bot)
        return
    if action == "stats":
        s = store.stats()
        lines = [
            f"{em('card')} <b>Статистика</b>\n",
            f"Всего заказов: <b>{s['total_orders']}</b>",
            f"Выдано: <b>{s['delivered_count']}</b>",
            f"Выручка: <b>{s['revenue']} ₽</b>\n",
        ]
        for st, v in s["by_status"].items():
            lines.append(f"• {st}: {v['count']} ({v['sum']} ₽)")
        await call.message.answer("\n".join(lines), reply_markup=admin_panel_kb(is_owner))
        return

    if not is_owner:
        await call.message.answer("Только владелец.")
        return

    if action == "prices":
        lines = [f"<b>Цены (USD)</b>\n1 USD = {STARS_PER_USD} Stars\n"]
        rows = []
        for cat, sec in CATALOG.items():
            if not sec.get("products"):
                continue
            lines.append(f"\n<b>{sec['title']}</b>")
            for p in sec["products"]:
                price = store.get_price(p["id"])
                stars = usd_to_stars(price)
                lines.append(f"• {p['title']} — <b>${price}</b> ({stars} Stars)")
                rows.append((button(f"{p['id']} → ${price}", f"adm:setprice:{p['id']}", "money"),))
        rows.append((button("Назад", "adm:home", "home"),))
        await call.message.answer("\n".join(lines), reply_markup=keyboard(*rows))
        return

    if action.startswith("setprice:"):
        pid = action.partition(":")[2]
        if pid not in PRODUCTS:
            await call.message.answer("Нет товара")
            return
        admin_input[uid] = {"kind": "price", "product_id": pid}
        cur = store.get_price(pid)
        await call.message.answer(
            f"Новая цена в <b>USD</b> для <b>{escape(PRODUCTS[pid]['title'])}</b>\n"
            f"Сейчас: <b>${cur}</b> = {usd_to_stars(cur)} Stars\n"
            f"Отправь число (например 5 или 2.5):"
        )
        return

    if action == "promos":
        promos = store.list_promos()
        lines = [f"{em('promo')} <b>Промокоды</b>\n"]
        for p in promos or []:
            st = "✅" if p["active"] else "❌"
            lines.append(f"{st} <code>{p['code']}</code> −{p['percent']}% "
                         f"({p['used_count']}/{p['max_uses'] or '∞'})")
        if not promos:
            lines.append("Пусто.")
        await call.message.answer(
            "\n".join(lines),
            reply_markup=keyboard(
                (button("➕ Добавить", "adm:addpromo", "promo"),),
                (button("⬅️ Назад", "adm:home", "home"),),
            ),
        )
        return

    if action == "addpromo":
        admin_input[uid] = {"kind": "promo"}
        await call.message.answer("Формат: <code>КОД ПРОЦЕНТ [лимит]</code>\nПример: <code>SALE20 20 50</code>")
        return

    if action == "mods":
        mods = store.list_moderators()
        lines = [f"{em('user')} <b>Модераторы</b>\n"]
        for m in mods or []:
            lines.append(f"• <code>{m['telegram_id']}</code> @{escape(m.get('username') or '—')}")
        if not mods:
            lines.append("Пусто.")
        await call.message.answer(
            "\n".join(lines),
            reply_markup=keyboard(
                (button("➕ Добавить", "adm:addmod", "user"),),
                (button("➖ Удалить", "adm:delmod", "warning"),),
                (button("⬅️ Назад", "adm:home", "home"),),
            ),
        )
        return

    if action == "addmod":
        admin_input[uid] = {"kind": "addmod"}
        await call.message.answer("Telegram ID модератора:")
        return
    if action == "delmod":
        admin_input[uid] = {"kind": "delmod"}
        await call.message.answer("Telegram ID для удаления:")
        return

    if action == "db":
        cfg = game_config()
        await call.message.answer(
            f"{em('settings')} <b>MySQL</b>\n"
            f"Хост: <code>{escape(cfg.host)}:{cfg.port}</code>\n"
            f"Юзер: <code>{escape(cfg.user)}</code>\n"
            f"БД: <code>{escape(cfg.database)}</code>\n"
            f"Таблица: <code>{escape(cfg.table)}</code>\n"
            f"Skin: <code>{escape(cfg.skin_col)}</code> | "
            f"Password: <code>{escape(cfg.password_col)}</code>\n\n"
            "Отправь: <code>Логин Пароль Имя_базы</code>",
            reply_markup=keyboard(
                (button("Сменить хост", "adm:host", "settings"),),
                (button("Проверить", "adm:probe", "check"),),
                (button("⬅️ Назад", "adm:home", "home"),),
            ),
        )
        admin_input[uid] = {"kind": "db"}
        return

    if action == "host":
        admin_input[uid] = {"kind": "host"}
        await call.message.answer("Хост:порт (пример <code>1.2.3.4:3306</code>):")
        return

    if action == "probe":
        try:
            res = await asyncio.to_thread(mysql_probe, game_config(), True)
            await call.message.answer(f"{em('check')} {res}")
        except GrantError as e:
            await call.message.answer(f"{em('warning')} {escape(str(e))}")
        return

    if action == "photos":
        rows = [(button(CATALOG[c]["title"], f"adm:photo:{c}", "card"),) for c in CATALOG]
        rows.append((button("⬅️ Назад", "adm:home", "home"),))
        await call.message.answer("Категория для фото:", reply_markup=keyboard(*rows))
        return

    if action.startswith("photo:"):
        cat = action.partition(":")[2]
        if cat not in CATALOG:
            return
        admin_input[uid] = {"kind": "photo", "category": cat}
        await call.message.answer(f"Пришли фото для «{CATALOG[cat]['title']}»")
        return


# ─── Approve / Decline ───────────────────────────────────────────────────────
async def _notify_password(bot: Bot, order: dict, pwd: str):
    text = (
        f"{em('key')} <b>Пароль сброшен!</b>\n\n"
        f"Заказ #{order['id']}\n"
        f"Ник: <code>{escape(order['nickname'])}</code>\n"
        f"Новый пароль: <code>{escape(pwd)}</code>\n\n"
        f"⚠️ <b>После входа в аккаунт сразу смени пароль</b> в настройках.\n"
        f"Никому не отправляй этот пароль."
    )
    await bot.send_message(order["telegram_id"], text)
    try:
        await bot.send_message(
            OWNER_ID,
            f"{em('key')} Сброс пароля #{order['id']} → <code>{escape(order['nickname'])}</code> "
            f"(пароль отправлен игроку)",
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("ok:"))
async def approve(call: CallbackQuery, bot: Bot):
    if not can_review(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        order_id = int(call.data.partition(":")[2])
    except ValueError:
        await call.answer("Ошибка", show_alert=True)
        return
    if not store.claim(order_id):
        await call.answer("Уже обработан", show_alert=True)
        return
    order = store.get(order_id)
    await call.answer("Выдаю…")
    try:
        result = await asyncio.to_thread(grant_to_game, order, game_config())
    except GrantError as exc:
        store.retry(order_id, str(exc))
        await call.message.answer(
            f"{em('warning')} <b>#{order_id} не выдан</b>\n{escape(str(exc))}",
            reply_markup=review_kb(order_id),
        )
        return
    except Exception:
        logging.exception("grant #%s", order_id)
        store.retry(order_id, "Ошибка")
        await call.message.answer(f"Ошибка #{order_id}", reply_markup=review_kb(order_id))
        return

    store.delivered(order_id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if isinstance(result, str) and result.startswith("pwd:"):
        pwd = result[4:]
        await call.message.answer(
            f"{em('check')} <b>#{order_id} — пароль сброшен</b>\n"
            f"Ник: <code>{escape(order['nickname'])}</code>\n"
            f"Пароль отправлен игроку."
        )
        try:
            await _notify_password(bot, order, pwd)
        except Exception:
            logging.exception("notify pwd")
            await call.message.answer(f"Не удалось отправить пароль игроку. Пароль: <code>{escape(pwd)}</code>")
        return

    await call.message.answer(
        f"{em('check')} <b>Заказ #{order_id} выдан!</b>\n"
        f"Игрок: <code>{escape(order['nickname'])}</code>\n"
        f"Результат: <code>{result}</code>"
    )
    try:
        await bot.send_message(
            order["telegram_id"],
            f"{em('check')} <b>Покупка #{order_id} подтверждена!</b>\n"
            f"Выдано: <b>{escape(order['item_title'])}</b>\n"
            f"Ник: <code>{escape(order['nickname'])}</code>\n\n"
            "Если в игре — перезайди, чтобы увидеть изменения.",
        )
    except Exception:
        logging.exception("notify buyer")


@dp.callback_query(F.data.startswith("no:"))
async def decline(call: CallbackQuery, bot: Bot):
    if not can_review(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        order_id = int(call.data.partition(":")[2])
    except ValueError:
        await call.answer("Ошибка", show_alert=True)
        return
    if not store.reject(order_id):
        await call.answer("Уже обработан", show_alert=True)
        return
    await call.answer("Отклонено")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"{em('warning')} Заказ #{order_id} отклонён.")
    order = store.get(order_id)
    try:
        await bot.send_message(
            order["telegram_id"],
            f"{em('warning')} <b>Чек по заказу #{order_id} не принят.</b>\n"
            "Уточни у поддержки и оформи заказ заново.",
        )
    except Exception:
        pass


# ─── Text / media ────────────────────────────────────────────────────────────
@dp.message(F.text)
async def text_handler(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    text = (message.text or "").strip()

    # --- onboarding registration ---
    if uid in waiting_register:
        nick = text
        if not re.fullmatch(r"[A-Za-z0-9_\[\]-]{3,24}", nick):
            await message.answer("Ник 3–24 символа: латиница, цифры, _ - []")
            return
        exists = await asyncio.to_thread(account_exists, nick, game_config())
        if not exists:
            await message.answer(
                f"{em('warning')} Аккаунт <code>{escape(nick)}</code> не найден в базе сервера.\n"
                f"Проверь ник и отправь снова (или сначала зарегистрируйся в игре)."
            )
            return
        waiting_register.discard(uid)
        store.set_nickname(uid, nick, registered=True)
        await message.answer(
            f"{em('check')} Аккаунт <code>{escape(nick)}</code> подтверждён!\n"
            f"Добро пожаловать в <b>WAIP SHOP</b>.",
        )
        await home(message)
        return

    # --- admin stages ---
    if can_review(uid) and admin_input.get(uid):
        stage = admin_input[uid]
        kind = stage.get("kind")
        if kind == "price" and uid == OWNER_ID:
            try:
                price = float(text.replace(",", "."))
                if not 0.01 <= price <= 10**7:
                    raise ValueError
            except ValueError:
                await message.answer("Число USD от 0.01 (например 5 или 2.5)")
                return
            store.set_price(stage["product_id"], price)
            admin_input.pop(uid, None)
            stars = usd_to_stars(price)
            await message.answer(
                f"{PRODUCTS[stage['product_id']]['title']} → <b>${price}</b> "
                f"({stars} Stars)",
                reply_markup=admin_panel_kb(True),
            )
            return
        if kind == "promo" and uid == OWNER_ID:
            parts = text.split()
            if len(parts) < 2:
                await message.answer("КОД ПРОЦЕНТ [лимит]")
                return
            try:
                percent = int(parts[1])
                max_uses = int(parts[2]) if len(parts) > 2 else 0
                if not 1 <= percent <= 90:
                    raise ValueError
            except ValueError:
                await message.answer("Процент 1–90")
                return
            store.add_promo(parts[0], percent, max_uses)
            admin_input.pop(uid, None)
            await message.answer(f"{em('check')} Промокод <code>{parts[0].upper()}</code> −{percent}%")
            return
        if kind == "addmod" and uid == OWNER_ID:
            try:
                mid = int(text)
            except ValueError:
                await message.answer("Нужен числовой ID")
                return
            store.add_moderator(mid)
            admin_input.pop(uid, None)
            await message.answer(f"{em('check')} Модератор <code>{mid}</code> добавлен")
            return
        if kind == "delmod" and uid == OWNER_ID:
            try:
                mid = int(text)
            except ValueError:
                await message.answer("Нужен ID")
                return
            ok = store.remove_moderator(mid)
            admin_input.pop(uid, None)
            await message.answer("Удалён" if ok else "Не найден")
            return
        if kind == "db" and uid == OWNER_ID:
            parts = text.split()
            if len(parts) != 3:
                await message.answer("Логин Пароль Имя_базы")
                return
            store.set_settings({
                "mysql_user": parts[0],
                "mysql_password": parts[1],
                "mysql_database": parts[2],
            })
            admin_input.pop(uid, None)
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer(f"{em('check')} MySQL сохранён")
            return
        if kind == "host" and uid == OWNER_ID:
            m = re.fullmatch(r"([a-zA-Z0-9._-]{1,253})(?::(\d{1,5}))?", text)
            if not m:
                await message.answer("host:port")
                return
            store.set_settings({"mysql_host": m.group(1), "mysql_port": str(int(m.group(2) or 3306))})
            admin_input.pop(uid, None)
            await message.answer(f"{em('check')} Хост обновлён")
            return
        return

    # --- promo ---
    if waiting_for_promo.get(uid):
        waiting_for_promo.pop(uid, None)
        promo = store.get_promo(text)
        if not promo:
            await message.answer(f"{em('warning')} Промокод не найден")
            return
        if promo["max_uses"] > 0 and promo["used_count"] >= promo["max_uses"]:
            await message.answer(f"{em('warning')} Лимит исчерпан")
            return
        waiting_pay[uid] = waiting_pay.get(uid) or {}
        waiting_pay[uid]["promo"] = promo["code"]
        await message.answer(
            f"{em('check')} <code>{promo['code']}</code> (−{promo['percent']}%) "
            f"применится к следующему заказу."
        )
        return

    # --- nickname for order (fallback) ---
    if uid in waiting_for_nickname:
        code = waiting_for_nickname.pop(uid)
        item = store.product(code)
        if not item:
            await message.answer("Товар не найден")
            return
        nick = text
        if not re.fullmatch(r"[A-Za-z0-9_\[\]-]{3,24}", nick):
            waiting_for_nickname[uid] = code
            await message.answer("Ник 3–24: латиница, цифры, _ - []")
            return
        if not await asyncio.to_thread(account_exists, nick, game_config()):
            waiting_for_nickname[uid] = code
            await message.answer(f"{em('warning')} Аккаунт <code>{escape(nick)}</code> не найден")
            return
        promo = (waiting_pay.get(uid) or {}).get("promo")
        waiting_pay[uid] = {"code": code, "nick": nick, "promo": promo}
        store.set_nickname(uid, nick)
        await message.answer(
            f"{em('check')} Ник: <code>{escape(nick)}</code>\n"
            f"{escape(item['title'])} — {item['price']} ₽\n\nСпособ оплаты:",
            reply_markup=pay_method_kb(code),
        )
        return


@dp.message(F.photo | F.document)
async def receipt(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    uid = message.from_user.id

    if uid == OWNER_ID and admin_input.get(uid, {}).get("kind") == "photo":
        stage = admin_input.pop(uid)
        file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        store.set_photo(stage["category"], file_id)
        await message.answer(f"{em('check')} Фото сохранено")
        return

    order = store.active(uid)
    if not order or order["status"] != "awaiting_receipt":
        await message.answer("Сначала создай заказ. Сейчас фото не к чему привязать.")
        return
    if message.photo:
        file = message.photo[-1]
        kind = "photo"
    else:
        file = message.document
        if file.mime_type not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
            await message.answer("Нужно фото или PDF")
            return
        kind = "document"
    try:
        ok = store.submit_receipt(order["id"], uid, file.file_id, file.file_unique_id, kind)
    except ValueError as exc:
        await message.answer(f"{em('warning')} {escape(str(exc))}")
        return
    if not ok:
        await message.answer("Заказ уже изменился")
        return
    await message.answer(
        f"{em('check')} Чек по заказу #{order['id']} отправлен на проверку."
    )
    caption = order_description(store.get(order["id"]))
    targets = {OWNER_ID}
    for m in store.list_moderators():
        targets.add(m["telegram_id"])
    for tid in targets:
        try:
            if kind == "photo":
                await bot.send_photo(tid, file.file_id, caption=caption, reply_markup=review_kb(order["id"]))
            else:
                await bot.send_document(tid, file.file_id, caption=caption, reply_markup=review_kb(order["id"]))
        except Exception:
            logging.exception("notify %s", tid)


# ─── Crypto poller ───────────────────────────────────────────────────────────
async def crypto_poller(bot: Bot):
    while True:
        try:
            rows = store.db.execute(
                "SELECT id, cryptobot_invoice_id, telegram_id, item_title, nickname, kind "
                "FROM orders WHERE status='awaiting_crypto' AND cryptobot_invoice_id IS NOT NULL"
            ).fetchall()
            for row in rows:
                oid = row["id"]
                try:
                    status = await asyncio.to_thread(check_crypto_invoice, row["cryptobot_invoice_id"])
                except Exception:
                    logging.exception("check inv")
                    continue
                if status == "paid":
                    if not store.mark_paid_crypto(oid):
                        continue
                    await fulfill_paid_order(bot, oid, source="crypto")
                elif status == "expired":
                    store.cancel(oid, row["telegram_id"])
                    try:
                        await bot.send_message(
                            row["telegram_id"],
                            f"{em('warning')} Счёт по заказу #{oid} истёк.",
                        )
                    except Exception:
                        pass
        except Exception:
            logging.exception("crypto_poller")
        await asyncio.sleep(20)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    logging.info("WAIP SHOP @%s", me.username)
    if CRYPTOBOT_TOKEN:
        asyncio.create_task(crypto_poller(bot))

    # Web App HTTP API (нужен HTTPS-прокси / tunnel → WEBAPP_URL)
    try:
        from web_server import start_web_server

        async def _stars_link(order: dict) -> str:
            return await create_stars_invoice_link_with_bot(bot, order)

        ctx = {
            "bot_token": BOT_TOKEN,
            "bot": bot,
            "store": store,
            "catalog": CATALOG,
            "products": PRODUCTS,
            "get_game_config": game_config,
            "fetch_player": fetch_player,
            "account_exists": account_exists,
            "is_subscribed": is_subscribed,
            "create_crypto_invoice": create_crypto_invoice,
            "create_stars_invoice_link": _stars_link,
            "grant_to_game": grant_to_game,
            "notify_password": _notify_password,
            "cryptobot_token": CRYPTOBOT_TOKEN,
            "stars_user": STARS_USER,
            "owner_id": OWNER_ID,
            "channel_username": CHANNEL_USERNAME,
            "apk_url": APK_DOWNLOAD_URL,
            "stars_per_usd": STARS_PER_USD,
            "money_per_usd": MONEY_PER_USD,
            "money_min_usd": MONEY_MIN_USD,
            # True = после «Я отправил подарок» выдаём сразу (без ручного ok:)
            "auto_grant_stars_gift": True,
            "asyncio": asyncio,
        }
        await start_web_server(ctx, WEBAPP_HOST, WEBAPP_PORT)
        if WEBAPP_URL:
            logging.info("WebApp button URL: %s", WEBAPP_URL)
            try:
                from aiogram.types import MenuButtonWebApp
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="WAIP SHOP", web_app=WebAppInfo(url=WEBAPP_URL))
                )
            except Exception:
                logging.exception("set_chat_menu_button failed")
        else:
            logging.info("WEBAPP_URL пуст — кнопка Mini App скрыта (сервер всё равно слушает порт %s)", WEBAPP_PORT)
    except Exception:
        logging.exception("WebApp server failed to start (бот продолжит работу без Mini App)")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
