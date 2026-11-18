"""WAIP STUDIO — сайт, где продаются боты-автодонаты для CRMP-проектов.

Роли:
  * admin      — главный админ: видит все заказы, добавляет модераторов, отмечает выплаты.
                 Свои заказы создаёт без процентов: 100% остаётся на балансе бота студии.
  * moderator  — создаёт заказы (данные клиента), видит только свои заказы и свой заработок.

Поток заказа:
  1. Модератор заполняет данные клиента → «Проверить» → «Создать заказ».
  2. Отдельный бот студии (STUDIO_BOT_TOKEN) шлёт клиенту (ID администратора) счёт на 250 ⭐
     с кнопкой «Оплатить». Если клиент ещё не писал боту — модератор получает ссылку для клиента.
  3. Оплата приходит на баланс бота студии. Бот фиксирует долю модератора (по умолчанию 60%)
     и остаток бота (40%), сразу пишет модератору в Telegram и запускает создание бота клиента.
  4. Клиент получает сообщение со ссылкой на готовый бот.

ВАЖНО: Telegram не даёт боту переводить Stars другому пользователю. Поэтому доля модератора
записывается в журнал (кому и сколько должен бот), модератор мгновенно получает уведомление,
а админ отмечает «выплачено» после перевода (подарок/вывод через Fragment и т.п.).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from aiohttp import web

from tenants import TenantError, logo_extension, normalize_fields

STUDIO_DIR = Path(__file__).resolve().parent / "studio_web"
LOGIN_RE = re.compile(r"[A-Za-z0-9_]{3,32}")
SESSION_DAYS = 7
TIME_FMT = "%Y-%m-%dT%H:%M:%S"
FIELDS = ("title", "theme", "token", "admin_id", "db_host", "db_port", "db_user",
          "db_password", "db_name", "hash_method")


def _now() -> str:
    return datetime.now(timezone.utc).strftime(TIME_FMT)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(12)
    dig = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${dig}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, _dig = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


class Studio:
    def __init__(self, data_dir: Path, fernet, tenants, bot_token: str, public_base: str,
                 admin_login: str, admin_password: str, price: int = 250, mod_percent: int = 60):
        from aiogram import Bot, Dispatcher
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "studio.sqlite3"
        self.fernet = fernet
        self.tenants = tenants
        self.public_base = public_base.rstrip("/")
        self.price = max(1, int(price))
        self.mod_percent = min(100, max(0, int(mod_percent)))
        self.bot = Bot(bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self.bot_username = ""
        self.bot_id = int(bot_token.split(":")[0])
        self.attempts: dict[str, list[float]] = {}
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              login TEXT UNIQUE NOT NULL COLLATE NOCASE,
              pass_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'moderator',
              tg_id INTEGER NOT NULL DEFAULT 0,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT UNIQUE NOT NULL,
              creator_id INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'awaiting_payment',
              title TEXT NOT NULL,
              customer_id INTEGER NOT NULL,
              bot_username TEXT NOT NULL DEFAULT '',
              fields_enc TEXT NOT NULL DEFAULT '',
              price INTEGER NOT NULL,
              mod_share INTEGER NOT NULL DEFAULT 0,
              bot_share INTEGER NOT NULL DEFAULT 0,
              charge_id TEXT UNIQUE,
              tenant_id INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              paid_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ledger(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              order_id INTEGER UNIQUE NOT NULL,
              stars INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'owed',
              created_at TEXT NOT NULL,
              paid_at TEXT
            );
            """
        )
        self.db.commit()
        if admin_login and admin_password:
            self._upsert_admin(admin_login, admin_password)
        self._register_bot_handlers()

    # ── пользователи ────────────────────────────────────────────────────────
    def _upsert_admin(self, login: str, password: str) -> None:
        login = login.strip().lstrip("@")
        row = self.db.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE users SET login=?, pass_hash=?, active=1 WHERE id=?",
                            (login, hash_password(password), row["id"]))
        else:
            self.db.execute("INSERT OR REPLACE INTO users(login,pass_hash,role,created_at) VALUES(?,?, 'admin', ?)",
                            (login, hash_password(password), _now()))
        self.db.commit()

    def _enc(self, text: str) -> str:
        return self.fernet.encrypt(text.encode()).decode()

    def _dec(self, token: str) -> str:
        return self.fernet.decrypt(token.encode()).decode()

    # ── сессии ──────────────────────────────────────────────────────────────
    def _throttle(self, ip: str) -> bool:
        now = time.time()
        hits = [t for t in self.attempts.get(ip, []) if now - t < 600]
        self.attempts[ip] = hits
        return len(hits) >= 8

    def _new_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        self.db.execute("INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)",
                        (hashlib.sha256(token.encode()).hexdigest(), user_id,
                         time.time() + SESSION_DAYS * 86400))
        self.db.commit()
        return token

    def _user_from(self, request: web.Request):
        token = request.cookies.get("studio_sid", "")
        if not token:
            return None
        row = self.db.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires>? AND u.active=1",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        ).fetchone()
        return row

    # ── деньги ──────────────────────────────────────────────────────────────
    def split(self, creator_role: str) -> tuple[int, int]:
        mod = self.price * self.mod_percent // 100 if creator_role == "moderator" else 0
        return mod, self.price - mod

    def owed(self, user_id: int) -> int:
        return int(self.db.execute(
            "SELECT COALESCE(SUM(stars),0) n FROM ledger WHERE user_id=? AND status='owed'",
            (user_id,)).fetchone()["n"])

    def earned_total(self, user_id: int) -> int:
        return int(self.db.execute(
            "SELECT COALESCE(SUM(stars),0) n FROM ledger WHERE user_id=?", (user_id,)).fetchone()["n"])

    # ── сериализация ────────────────────────────────────────────────────────
    def pay_link(self, code: str) -> str:
        return f"https://t.me/{self.bot_username}?start=pay_{code}" if self.bot_username else ""

    def order_dict(self, r, for_admin: bool) -> dict:
        creator = self.db.execute("SELECT login FROM users WHERE id=?", (r["creator_id"],)).fetchone()
        out = {
            "id": r["id"], "status": r["status"], "title": r["title"],
            "customer_id": r["customer_id"], "bot_username": r["bot_username"],
            "price": r["price"], "mod_share": r["mod_share"], "bot_share": r["bot_share"],
            "creator": creator["login"] if creator else "?", "created_at": r["created_at"],
            "paid_at": r["paid_at"], "error": r["error"],
            "bot_url": f"https://t.me/{r['bot_username']}" if r["bot_username"] and r["status"] == "done" else "",
        }
        if r["status"] == "awaiting_payment":
            out["pay_link"] = self.pay_link(r["code"])
        return out

    # ── заказ ───────────────────────────────────────────────────────────────
    async def create_order(self, user, raw: dict) -> dict:
        data = {k: raw.get(k, "") for k in FIELDS}
        data["db_port"] = data["db_port"] or "3306"
        data["hash_method"] = data["hash_method"] or "md5"
        d = normalize_fields(data)  # проверка формата
        logo_b64 = raw.get("logo") or ""
        if logo_b64:
            try:
                logo = base64.b64decode(logo_b64.split(",", 1)[-1], validate=True)
            except Exception:
                raise TenantError("Логотип не распознан")
            if len(logo) > 1_500_000 or not logo_extension(logo):
                raise TenantError("Логотип: PNG/JPG/WEBP до 1.5 МБ")
        check = await self.tenants.validate(data)
        if not check.get("ok"):
            raise TenantError("Проверка не пройдена: " + "; ".join(
                f"{c['name']}: {c['msg']}" for c in check["checks"] if not c["ok"]))
        mod, bot_share = self.split(user["role"])
        code = secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")
        stored = dict(data)
        stored["logo"] = logo_b64
        cur = self.db.execute(
            "INSERT INTO orders(code,creator_id,title,customer_id,bot_username,fields_enc,price,"
            "mod_share,bot_share,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (code, user["id"], d["title"], d["admin_id"], check.get("bot_username", ""),
             self._enc(json.dumps(stored)), self.price, mod, bot_share, _now()),
        )
        self.db.commit()
        oid = int(cur.lastrowid)
        sent = await self.send_invoice(oid)
        row = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        res = self.order_dict(row, True)
        res["invoice_sent"] = sent
        return res

    async def send_invoice(self, order_id: int, chat_id: int | None = None) -> bool:
        from aiogram.types import LabeledPrice
        r = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not r or r["status"] != "awaiting_payment":
            return False
        try:
            await self.bot.send_invoice(
                chat_id=chat_id or r["customer_id"],
                title=f"Автодонат-бот «{r['title']}»"[:32],
                description=(f"Создание бота автодоната «{r['title']}» для вашего проекта. "
                             f"После оплаты бот запустится автоматически.")[:255],
                payload=f"studio:{r['code']}",
                currency="XTR",
                prices=[LabeledPrice(label="Бот автодоната", amount=int(r["price"]))],
            )
            return True
        except Exception as exc:
            logging.info("studio invoice not delivered order=%s: %s", order_id, exc)
            return False

    def _begin_create(self, order_id: int) -> None:
        r = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not r or not r["fields_enc"]:
            return
        f = json.loads(self._dec(r["fields_enc"]))
        logo = f.pop("logo", "") or None
        try:
            job_id = self.tenants.start_create(int(r["customer_id"]), f, logo, studio=True)
        except TenantError as exc:
            self._fail(order_id, str(exc))
            return
        self.db.execute("UPDATE orders SET status='creating', error='' WHERE id=?", (order_id,))
        self.db.commit()
        asyncio.create_task(self._watch(order_id, job_id))

    def _fail(self, order_id: int, msg: str) -> None:
        self.db.execute("UPDATE orders SET status='failed', error=? WHERE id=?", (msg[:300], order_id))
        self.db.commit()

    async def _watch(self, order_id: int, job_id: str) -> None:
        for _ in range(240):
            job = self.tenants.jobs.get(job_id)
            if job and job["status"] != "running":
                break
            await asyncio.sleep(2)
        else:
            job = None
        r = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            return
        if job and job["status"] == "done":
            res = job["result"] or {}
            self.db.execute(
                "UPDATE orders SET status='done', tenant_id=?, bot_username=?, fields_enc='', error='' WHERE id=?",
                (int(res.get("id", 0)), res.get("bot_username", ""), order_id))
            self.db.commit()
            link = res.get("bot_url") or ""
            await self._safe_send(r["customer_id"],
                f"<b>✅ Ваш бот автодоната готов!</b>\n\n«{escape(r['title'])}» — {link}\n"
                f"Откройте бота и нажмите кнопку меню, чтобы войти в магазин.")
            await self._notify_creator(r["creator_id"],
                f"✅ Заказ #{order_id} «{escape(r['title'])}» выполнен. Бот: {link}")
        else:
            err = (job or {}).get("error") or "Создание не завершилось вовремя"
            self._fail(order_id, err)
            await self._notify_creator(r["creator_id"],
                f"⚠️ Заказ #{order_id} оплачен, но бот не создан: {escape(err)}\nАдмин сможет повторить создание на сайте.")
            admin = self.db.execute("SELECT tg_id FROM users WHERE role='admin'").fetchone()
            if admin and admin["tg_id"]:
                await self._safe_send(admin["tg_id"], f"⚠️ Заказ #{order_id} оплачен, создание бота упало: {escape(err)}")

    async def _safe_send(self, chat_id: int, text: str) -> bool:
        try:
            await self.bot.send_message(chat_id, text, disable_web_page_preview=True)
            return True
        except Exception:
            return False

    async def _notify_creator(self, user_id: int, text: str) -> None:
        u = self.db.execute("SELECT tg_id FROM users WHERE id=?", (user_id,)).fetchone()
        if u and u["tg_id"]:
            await self._safe_send(u["tg_id"], text)

    # ── бот студии ──────────────────────────────────────────────────────────
    def _register_bot_handlers(self) -> None:
        from aiogram import F
        from aiogram.filters import CommandObject, CommandStart
        from aiogram.types import Message, PreCheckoutQuery

        @self.dp.message(CommandStart())
        async def on_start(message: Message, command: CommandObject):
            arg = (command.args or "").strip()
            if arg.startswith("pay_"):
                r = self.db.execute("SELECT * FROM orders WHERE code=?", (arg[4:],)).fetchone()
                if r and r["status"] == "awaiting_payment":
                    await self.send_invoice(r["id"], chat_id=message.chat.id)
                else:
                    await message.answer("Этот заказ уже оплачен или недействителен.")
                return
            uname = (message.from_user.username or "").lower()
            if uname:
                u = self.db.execute("SELECT * FROM users WHERE lower(login)=? AND active=1", (uname,)).fetchone()
                if u:
                    self.db.execute("UPDATE users SET tg_id=? WHERE id=?", (message.from_user.id, u["id"]))
                    self.db.commit()
                    await message.answer("✅ Уведомления подключены. Сюда придут сообщения об оплатах и твоих начислениях.")
                    return
            await message.answer("Это бот оплаты автодонатов. Оплатить заказ можно по ссылке от менеджера.")

        @self.dp.pre_checkout_query()
        async def on_pre(query: PreCheckoutQuery):
            payload = query.invoice_payload or ""
            r = self.db.execute("SELECT * FROM orders WHERE code=?", (payload[7:],)).fetchone() \
                if payload.startswith("studio:") else None
            if not r or r["status"] != "awaiting_payment" or query.currency != "XTR" \
                    or query.total_amount != int(r["price"]):
                await query.answer(ok=False, error_message="Заказ уже оплачен или недействителен.")
                return
            await query.answer(ok=True)

        @self.dp.message(F.successful_payment)
        async def on_paid(message: Message):
            sp = message.successful_payment
            payload = sp.invoice_payload or ""
            if not payload.startswith("studio:"):
                return
            await self._on_payment(payload[7:], sp.total_amount, sp.currency,
                                   sp.telegram_payment_charge_id, message.chat.id)

    async def _on_payment(self, code: str, amount: int, currency: str, charge_id: str, chat_id: int) -> None:
        r = self.db.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
        if not r or currency != "XTR" or amount < int(r["price"]):
            logging.warning("studio payment mismatch code=%s amount=%s", code, amount)
            return
        cur = self.db.execute(
            "UPDATE orders SET status='paid', paid_at=?, charge_id=? WHERE id=? AND status='awaiting_payment'",
            (_now(), charge_id, r["id"]))
        self.db.commit()
        if not cur.rowcount:
            return  # повторное уведомление
        oid = int(r["id"])
        if r["mod_share"] > 0:
            self.db.execute("INSERT OR IGNORE INTO ledger(user_id,order_id,stars,created_at) VALUES(?,?,?,?)",
                            (r["creator_id"], oid, int(r["mod_share"]), _now()))
            self.db.commit()
            await self._notify_creator(
                r["creator_id"],
                f"💰 Заказ #{oid} «{escape(r['title'])}» оплачен!\n"
                f"Твоя доля: <b>{r['mod_share']} ⭐</b> ({self.mod_percent}%). В балансе бота остаётся {r['bot_share']} ⭐.\n"
                f"Выплату отметит админ на сайте.")
        else:
            await self._notify_creator(r["creator_id"],
                f"💰 Заказ #{oid} «{escape(r['title'])}» оплачен: {r['price']} ⭐ на балансе бота.")
        await self._safe_send(chat_id, "✅ Оплата получена! Создаём вашего бота — это займёт около минуты. Пришлём ссылку.")
        admin = self.db.execute("SELECT tg_id FROM users WHERE role='admin'").fetchone()
        if admin and admin["tg_id"] and admin["tg_id"] != 0:
            await self._safe_send(admin["tg_id"],
                f"💰 Оплата заказа #{oid} «{escape(r['title'])}»: {r['price']} ⭐ "
                f"(модератору {r['mod_share']}, боту {r['bot_share']}).")
        self._begin_create(oid)

    async def start(self) -> None:
        me = await self.bot.get_me()
        self.bot_username = me.username or ""
        try:
            await self.bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            logging.exception("studio delete_webhook")
        asyncio.create_task(self.dp.start_polling(self.bot, handle_signals=False))
        for r in self.db.execute("SELECT id FROM orders WHERE status IN ('paid','creating')").fetchall():
            self._begin_create(int(r["id"]))
        logging.info("WAIP STUDIO бот @%s запущен, цена %s ⭐, модератору %s%%",
                     self.bot_username, self.price, self.mod_percent)

    # ── HTTP ────────────────────────────────────────────────────────────────
    def add_routes(self, app: web.Application) -> None:
        r = app.router
        r.add_get("/studio", self.h_index)
        r.add_get("/studio/", self.h_index)
        r.add_post("/studio/api/login", self.h_login)
        r.add_post("/studio/api/logout", self.h_logout)
        r.add_get("/studio/api/me", self.h_me)
        r.add_post("/studio/api/validate", self.h_validate)
        r.add_get("/studio/api/orders", self.h_orders)
        r.add_post("/studio/api/orders", self.h_order_create)
        r.add_post("/studio/api/orders/{id}/resend", self.h_resend)
        r.add_post("/studio/api/orders/{id}/retry", self.h_retry)
        r.add_post("/studio/api/orders/{id}/cancel", self.h_cancel)
        r.add_get("/studio/api/moderators", self.h_mods)
        r.add_post("/studio/api/moderators", self.h_mod_add)
        r.add_post("/studio/api/moderators/{id}/toggle", self.h_mod_toggle)
        r.add_post("/studio/api/moderators/{id}/password", self.h_mod_password)
        r.add_post("/studio/api/moderators/{id}/paid", self.h_mod_paid)

    @staticmethod
    def _json(data, status=200):
        return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})

    async def _body(self, request: web.Request) -> dict:
        if request.headers.get("X-Requested-With") != "studio":
            raise web.HTTPForbidden(text="bad request")
        try:
            d = await request.json()
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    def _auth(self, request: web.Request, admin: bool = False):
        u = self._user_from(request)
        if not u:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Нужен вход"}), content_type="application/json")
        if admin and u["role"] != "admin":
            raise web.HTTPForbidden(text=json.dumps({"error": "Только для админа"}), content_type="application/json")
        return u

    async def h_index(self, request):
        page = (STUDIO_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def h_login(self, request):
        body = await self._body(request)
        ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
        if self._throttle(ip):
            return self._json({"error": "Слишком много попыток. Подожди 10 минут."}, 429)
        login = str(body.get("login") or "").strip().lstrip("@")
        u = self.db.execute("SELECT * FROM users WHERE login=? AND active=1", (login,)).fetchone()
        if not u or not check_password(str(body.get("password") or ""), u["pass_hash"]):
            self.attempts.setdefault(ip, []).append(time.time())
            return self._json({"error": "Неверный логин или пароль"}, 401)
        token = self._new_session(u["id"])
        resp = self._json({"ok": True})
        secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
        resp.set_cookie("studio_sid", token, httponly=True, samesite="Lax", secure=secure,
                        max_age=SESSION_DAYS * 86400, path="/studio")
        return resp

    async def h_logout(self, request):
        await self._body(request)
        token = request.cookies.get("studio_sid", "")
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            self.db.commit()
        resp = self._json({"ok": True})
        resp.del_cookie("studio_sid", path="/studio")
        return resp

    async def h_me(self, request):
        u = self._auth(request)
        mod, bot_share = self.split(u["role"])
        return self._json({
            "login": u["login"], "role": u["role"], "owed": self.owed(u["id"]),
            "earned": self.earned_total(u["id"]), "price": self.price,
            "mod_percent": self.mod_percent, "my_share": mod, "bot_username": self.bot_username,
            "tg_linked": bool(u["tg_id"]),
        })

    async def h_validate(self, request):
        self._auth(request)
        body = await self._body(request)
        data = {k: body.get(k, "") for k in FIELDS}
        data["db_port"] = data["db_port"] or "3306"
        data["hash_method"] = data["hash_method"] or "md5"
        try:
            return self._json(await self.tenants.validate(data))
        except TenantError as exc:
            return self._json({"ok": False, "checks": [{"name": "Данные", "ok": False, "msg": str(exc)}]})

    async def h_orders(self, request):
        u = self._auth(request)
        if u["role"] == "admin":
            rows = self.db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 300").fetchall()
        else:
            rows = self.db.execute("SELECT * FROM orders WHERE creator_id=? ORDER BY id DESC LIMIT 300",
                                   (u["id"],)).fetchall()
        return self._json({"orders": [self.order_dict(r, u["role"] == "admin") for r in rows]})

    async def h_order_create(self, request):
        u = self._auth(request)
        body = await self._body(request)
        try:
            return self._json({"ok": True, "order": await self.create_order(u, body)})
        except TenantError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception:
            logging.exception("studio create_order")
            return self._json({"error": "Внутренняя ошибка"}, 500)

    def _own_order(self, request, u):
        try:
            oid = int(request.match_info["id"])
        except ValueError:
            raise web.HTTPNotFound()
        r = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not r or (u["role"] != "admin" and r["creator_id"] != u["id"]):
            raise web.HTTPNotFound()
        return r

    async def h_resend(self, request):
        u = self._auth(request)
        await self._body(request)
        r = self._own_order(request, u)
        return self._json({"ok": await self.send_invoice(r["id"]), "pay_link": self.pay_link(r["code"])})

    async def h_cancel(self, request):
        u = self._auth(request)
        await self._body(request)
        r = self._own_order(request, u)
        if r["status"] != "awaiting_payment":
            return self._json({"error": "Отменить можно только неоплаченный заказ"}, 400)
        self.db.execute("UPDATE orders SET status='cancelled', fields_enc='' WHERE id=?", (r["id"],))
        self.db.commit()
        return self._json({"ok": True})

    async def h_retry(self, request):
        u = self._auth(request, admin=True)
        await self._body(request)
        r = self._own_order(request, u)
        if r["status"] != "failed" or not r["fields_enc"]:
            return self._json({"error": "Повторить можно только упавший оплаченный заказ"}, 400)
        self._begin_create(r["id"])
        return self._json({"ok": True})

    async def h_mods(self, request):
        self._auth(request, admin=True)
        out = []
        for m in self.db.execute("SELECT * FROM users WHERE role='moderator' ORDER BY id DESC").fetchall():
            cnt = self.db.execute("SELECT COUNT(*) n FROM orders WHERE creator_id=? AND paid_at IS NOT NULL",
                                  (m["id"],)).fetchone()["n"]
            out.append({"id": m["id"], "login": m["login"], "active": bool(m["active"]),
                        "tg_linked": bool(m["tg_id"]), "paid_orders": cnt,
                        "owed": self.owed(m["id"]), "earned": self.earned_total(m["id"])})
        total_bot = int(self.db.execute(
            "SELECT COALESCE(SUM(bot_share),0) n FROM orders WHERE paid_at IS NOT NULL").fetchone()["n"])
        total_owed = int(self.db.execute("SELECT COALESCE(SUM(stars),0) n FROM ledger WHERE status='owed'").fetchone()["n"])
        return self._json({"moderators": out, "total_bot": total_bot, "total_owed": total_owed})

    async def h_mod_add(self, request):
        self._auth(request, admin=True)
        body = await self._body(request)
        login = str(body.get("login") or "").strip().lstrip("@")
        if not LOGIN_RE.fullmatch(login):
            return self._json({"error": "Логин = Telegram username (латиница, цифры, _; 3–32 символа)"}, 400)
        if self.db.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
            return self._json({"error": "Такой модератор уже есть"}, 400)
        password = secrets.token_urlsafe(9)
        self.db.execute("INSERT INTO users(login,pass_hash,role,created_at) VALUES(?,?, 'moderator', ?)",
                        (login, hash_password(password), _now()))
        self.db.commit()
        return self._json({"ok": True, "login": login, "password": password})

    async def h_mod_toggle(self, request):
        self._auth(request, admin=True)
        await self._body(request)
        mid = int(request.match_info["id"])
        self.db.execute("UPDATE users SET active=1-active WHERE id=? AND role='moderator'", (mid,))
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (mid,))
        self.db.commit()
        return self._json({"ok": True})

    async def h_mod_password(self, request):
        self._auth(request, admin=True)
        await self._body(request)
        mid = int(request.match_info["id"])
        password = secrets.token_urlsafe(9)
        self.db.execute("UPDATE users SET pass_hash=? WHERE id=? AND role='moderator'", (hash_password(password), mid))
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (mid,))
        self.db.commit()
        return self._json({"ok": True, "password": password})

    async def h_mod_paid(self, request):
        self._auth(request, admin=True)
        await self._body(request)
        mid = int(request.match_info["id"])
        amount = self.owed(mid)
        if not amount:
            return self._json({"error": "Нет долга перед этим модератором"}, 400)
        self.db.execute("UPDATE ledger SET status='paid', paid_at=? WHERE user_id=? AND status='owed'", (_now(), mid))
        self.db.commit()
        await self._notify_creator(mid, f"✅ Админ отметил выплату: <b>{amount} ⭐</b>. Спасибо за работу!")
        return self._json({"ok": True, "paid": amount})
