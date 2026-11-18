"""WAIP SHOP MASTER — создание дочерних магазинов (ботов) из WebApp.

Что делает модуль:
  * проверяет токен бота (Telegram getMe) и MySQL (подключение, таблица, колонки);
  * шифрует токен, пароль MySQL и секрет отчётности (Fernet, ключ WAIP_MASTER_FERNET_KEY);
  * создаёт магазин в фоне (job с процентом 0–100 и логами) и запускает его отдельным процессом
    (тот же main.py с WAIP_ROLE=child и своими переменными окружения);
  * проксирует WebApp дочернего магазина через мастер: https://<мастер>/t/<id>/webapp/;
  * принимает отчёты о продажах дочерних магазинов и раз в неделю выставляет комиссию
    20% от выручки в Telegram Stars (если выручки не было — комиссия не берётся);
  * за просрочку оплаты комиссии дольше GRACE_DAYS останавливает магазин.

Важно: Telegram Stars не переводятся автоматически с одного бота на другой. Комиссия
оплачивается через инвойс Stars, который выставляет МАСТЕР-бот администратору магазина.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

COMMISSION_PERCENT = 20
GRACE_DAYS = 3
MAX_TENANTS_PER_USER = 3
LOGO_MAX_BYTES = 1_500_000
TIME_FMT = "%Y-%m-%dT%H:%M:%S"
HASH_METHODS = ("md5", "sha1", "sha256", "double_md5", "plain", "whirlpool")
REQUIRED_COLUMNS = ("name", "level", "admin", "money", "skin", "password")
TOKEN_RE = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,}")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
HOST_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")
DBNAME_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
TABLE_NAME = "accounts"


class TenantError(Exception):
    """Ошибка, которую можно показать пользователю."""


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().strftime(TIME_FMT)


def logo_extension(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def normalize_fields(d: dict) -> dict:
    """Проверка формы. Возвращает очищенные значения или кидает TenantError."""
    if not isinstance(d, dict):
        raise TenantError("Некорректные данные")
    title = str(d.get("title") or "").strip()
    if not 2 <= len(title) <= 40:
        raise TenantError("Название магазина: от 2 до 40 символов")
    theme = str(d.get("theme") or "").strip()
    if not HEX_RE.fullmatch(theme):
        raise TenantError("Цвет оформления должен быть в формате #RRGGBB")
    token = str(d.get("token") or "").strip()
    if not TOKEN_RE.fullmatch(token):
        raise TenantError("Токен бота выглядит неверно (формат 123456:ABC…)")
    try:
        admin_id = int(str(d.get("admin_id") or "0").strip())
    except ValueError:
        admin_id = 0
    if admin_id <= 0:
        raise TenantError("Telegram ID администратора — числом, например 123456789")
    host = str(d.get("db_host") or "").strip()
    if not HOST_RE.fullmatch(host):
        raise TenantError("MySQL HOST указан неверно")
    try:
        port = int(str(d.get("db_port") or "3306").strip())
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise TenantError("MySQL PORT: число от 1 до 65535")
    user = str(d.get("db_user") or "").strip()
    if not 1 <= len(user) <= 64:
        raise TenantError("MySQL LOGIN не заполнен")
    password = str(d.get("db_password") or "")
    if not 1 <= len(password) <= 256:
        raise TenantError("MySQL PASSWORD не заполнен")
    name = str(d.get("db_name") or "").strip()
    if not DBNAME_RE.fullmatch(name):
        raise TenantError("Имя базы: только латиница, цифры и _")
    method = str(d.get("hash_method") or "md5").strip().lower()
    if method not in HASH_METHODS:
        raise TenantError("Метод хеширования пароля выбран неверно")
    return {
        "title": title,
        "theme": theme.lower(),
        "token": token,
        "admin_id": admin_id,
        "db": {
            "host": host, "port": port, "user": user,
            "password": password, "name": name, "hash": method,
        },
    }


def mysql_check(db: dict) -> tuple[bool, list[str]]:
    """Синхронная проверка MySQL (вызывается в отдельном потоке)."""
    msgs: list[str] = []
    try:
        import mysql.connector
    except ImportError:
        return False, ["На сервере не установлен mysql-connector-python"]
    try:
        conn = mysql.connector.connect(
            host=db["host"], port=db["port"], user=db["user"], password=db["password"],
            database=db["name"], connection_timeout=8, use_pure=True,
        )
    except Exception as exc:  # текст ошибки драйвера не содержит пароль
        return False, [f"Подключение к MySQL не удалось: {exc}"]
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        msgs.append("MySQL: подключение и SELECT 1 — ок")
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (db["name"], TABLE_NAME),
        )
        cols = {row[0] for row in cur.fetchall()}
        if not cols:
            return False, msgs + [f"Таблица «{TABLE_NAME}» не найдена в базе «{db['name']}»"]
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            return False, msgs + [f"В таблице нет колонок: {', '.join(missing)}"]
        msgs.append("Таблица и колонки найдены — ок")
        return True, msgs
    except Exception as exc:
        return False, msgs + [f"Ошибка запроса к MySQL: {exc}"]
    finally:
        conn.close()


def get_me(token: str) -> dict:
    """Проверка токена через Telegram Bot API getMe (синхронно)."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError:
        raise TenantError("Telegram отклонил токен — проверь, что он скопирован полностью")
    except Exception:
        raise TenantError("Не удалось связаться с Telegram API, попробуй ещё раз")
    if not data.get("ok") or not data.get("result", {}).get("is_bot"):
        raise TenantError("Этот токен не принадлежит боту")
    res = data["result"]
    return {"id": int(res["id"]), "username": res.get("username") or ""}


def set_bot_name(token: str, name: str) -> bool:
    """Имя бота в Telegram (setMyName). Не критично: при отказе Telegram создание продолжается."""
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyName",
        data=json.dumps({"name": name[:64]}).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception:
        return False


class TenantManager:
    def __init__(self, data_dir: Path, public_base: str, master_port: int,
                 fernet_key: str, master_bot_id: int = 0):
        from cryptography.fernet import Fernet  # noqa: WPS433 (проверка ключа при старте)

        self.fernet = Fernet(fernet_key.encode())
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.dir / "master.sqlite3"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tenants(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_id INTEGER NOT NULL,
              bot_id INTEGER UNIQUE NOT NULL,
              bot_username TEXT NOT NULL,
              title TEXT NOT NULL,
              theme TEXT NOT NULL,
              admin_id INTEGER NOT NULL,
              token_enc TEXT NOT NULL,
              secret_enc TEXT NOT NULL,
              db_enc TEXT NOT NULL,
              logo_path TEXT NOT NULL DEFAULT '',
              port INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sales(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              tenant_id INTEGER NOT NULL,
              external_order_id TEXT NOT NULL,
              stars INTEGER NOT NULL CHECK(stars > 0),
              created_at TEXT NOT NULL,
              UNIQUE(tenant_id, external_order_id)
            );
            CREATE TABLE IF NOT EXISTS commissions(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              tenant_id INTEGER NOT NULL,
              period_start TEXT NOT NULL,
              period_end TEXT NOT NULL,
              revenue_stars INTEGER NOT NULL,
              due_stars INTEGER NOT NULL,
              payload TEXT UNIQUE NOT NULL,
              status TEXT NOT NULL DEFAULT 'due',
              created_at TEXT NOT NULL,
              paid_at TEXT,
              UNIQUE(tenant_id, period_start)
            );
            """
        )
        self.db.commit()
        self.public_base = public_base.rstrip("/")
        self.master_port = master_port
        self.master_bot_id = master_bot_id
        self.procs: dict[int, asyncio.subprocess.Process] = {}
        self.supervisors: dict[int, asyncio.Task] = {}
        self.jobs: dict[str, dict] = {}
        self.bot = None
        self.session: aiohttp.ClientSession | None = None

    # ── шифрование ──────────────────────────────────────────────────────────
    def _enc(self, text: str) -> str:
        return self.fernet.encrypt(text.encode()).decode()

    def _dec(self, token: str) -> str:
        return self.fernet.decrypt(token.encode()).decode()

    # ── данные ──────────────────────────────────────────────────────────────
    def _row(self, tid: int):
        return self.db.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()

    def tenant_info(self, row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "bot_username": row["bot_username"],
            "status": row["status"],
            "webapp_url": f"{self.public_base}/t/{row['id']}/webapp/",
            "bot_url": f"https://t.me/{row['bot_username']}" if row["bot_username"] else "",
        }

    def list_for_owner(self, owner_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM tenants WHERE owner_id=? ORDER BY id DESC", (owner_id,)
        ).fetchall()
        return [self.tenant_info(r) for r in rows]

    # ── проверка формы (кнопка «Проверить данные») ─────────────────────────
    async def validate(self, raw: dict) -> dict:
        d = normalize_fields(raw)
        checks = []
        try:
            me = await asyncio.to_thread(get_me, d["token"])
            if me["id"] == self.master_bot_id:
                raise TenantError("Нельзя использовать токен основного бота")
            if self.db.execute("SELECT 1 FROM tenants WHERE bot_id=?", (me["id"],)).fetchone():
                raise TenantError("Этот бот уже подключён к WAIP SHOP")
            checks.append({"name": "Токен бота", "ok": True, "msg": f"@{me['username']}"})
        except TenantError as exc:
            checks.append({"name": "Токен бота", "ok": False, "msg": str(exc)})
            return {"ok": False, "checks": checks}
        ok, msgs = await asyncio.to_thread(mysql_check, d["db"])
        checks.append({"name": "MySQL", "ok": ok, "msg": " · ".join(msgs)})
        return {"ok": ok, "checks": checks, "bot_username": me["username"]}

    # ── создание магазина (job) ─────────────────────────────────────────────
    def start_create(self, owner_id: int, raw: dict, logo_b64: str | None) -> str:
        if not self.session:
            raise TenantError("Сервер не готов, попробуй через минуту")
        if not raw.get("agree"):
            raise TenantError("Нужно принять условия комиссии")
        if len(self.list_for_owner(owner_id)) >= MAX_TENANTS_PER_USER:
            raise TenantError(f"Лимит: не больше {MAX_TENANTS_PER_USER} ботов на аккаунт")
        d = normalize_fields(raw)
        logo = None
        if logo_b64:
            try:
                logo = base64.b64decode(logo_b64.split(",", 1)[-1], validate=True)
            except Exception:
                raise TenantError("Логотип не распознан")
            if len(logo) > LOGO_MAX_BYTES:
                raise TenantError("Логотип больше 1.5 МБ")
            if not logo_extension(logo):
                raise TenantError("Логотип должен быть PNG, JPG или WEBP")
        job_id = secrets.token_urlsafe(10)
        self.jobs[job_id] = {
            "id": job_id, "owner_id": owner_id, "status": "running", "percent": 0,
            "logs": [], "error": None, "result": None,
        }
        if len(self.jobs) > 200:
            for k in list(self.jobs)[:50]:
                if self.jobs[k]["status"] != "running":
                    self.jobs.pop(k, None)
        asyncio.create_task(self._run_job(self.jobs[job_id], d, logo))
        return job_id

    def job_for(self, job_id: str, owner_id: int) -> dict | None:
        job = self.jobs.get(job_id)
        if not job or job["owner_id"] != owner_id:
            return None
        return {k: job[k] for k in ("id", "status", "percent", "logs", "error", "result")}

    async def _run_job(self, job: dict, d: dict, logo: bytes | None) -> None:
        tid = None

        def log(msg: str, pct: int | None = None):
            job["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            job["logs"] = job["logs"][-200:]
            if pct is not None:
                job["percent"] = pct

        try:
            log("Данные формы приняты", 5)
            me = await asyncio.to_thread(get_me, d["token"])
            log(f"Токен подтверждён: @{me['username']}", 12)
            if me["id"] == self.master_bot_id:
                raise TenantError("Нельзя использовать токен основного бота")
            if self.db.execute("SELECT 1 FROM tenants WHERE bot_id=?", (me["id"],)).fetchone():
                raise TenantError("Этот бот уже подключён к WAIP SHOP")
            ok, msgs = await asyncio.to_thread(mysql_check, d["db"])
            for m in msgs:
                log(m)
            if not ok:
                raise TenantError("Проверка MySQL не пройдена")
            log("MySQL проверен", 30)

            secret = secrets.token_urlsafe(32)
            cur = self.db.execute(
                "INSERT INTO tenants(owner_id,bot_id,bot_username,title,theme,admin_id,"
                "token_enc,secret_enc,db_enc,port,created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                (job["owner_id"], me["id"], me["username"], d["title"], d["theme"],
                 d["admin_id"], self._enc(d["token"]), self._enc(secret),
                 self._enc(json.dumps(d["db"])), now_iso()),
            )
            self.db.commit()
            tid = int(cur.lastrowid)
            from config import TENANT_PORT_BASE  # noqa: WPS433
            port = TENANT_PORT_BASE + tid
            self.db.execute("UPDATE tenants SET port=? WHERE id=?", (port, tid))
            self.db.commit()
            log("Запись магазина создана, данные зашифрованы", 40)
            if await asyncio.to_thread(set_bot_name, d["token"], d["title"]):
                log(f"Имя бота: {d['title']}", 42)
            else:
                log("Имя бота не изменилось — при необходимости поменяй в @BotFather", 42)

            tdir = self.dir / str(tid)
            tdir.mkdir(parents=True, exist_ok=True)
            if logo:
                ext = logo_extension(logo)
                path = tdir / f"logo.{ext}"
                path.write_bytes(logo)
                self.db.execute("UPDATE tenants SET logo_path=? WHERE id=?", (str(path), tid))
                self.db.commit()
                log("Логотип сохранён", 50)
            else:
                log("Логотип не загружен — будет стандартный", 50)

            await self._start(tid)
            log("Процесс магазина запущен", 58)
            await self._wait_ready(tid, port, job, log)
            log("Магазин отвечает на запросы", 95)

            info = self.tenant_info(self._row(tid))
            job["result"] = info
            job["status"] = "done"
            log("Готово! Магазин работает.", 100)
        except Exception as exc:
            logging.exception("tenant job failed")
            job["status"] = "failed"
            job["error"] = str(exc) if isinstance(exc, TenantError) else "Внутренняя ошибка, см. логи Render"
            log(f"Ошибка: {job['error']}")
            if tid is not None:
                await self._remove(tid)

    async def _wait_ready(self, tid: int, port: int, job: dict, log) -> None:
        url = f"http://127.0.0.1:{port}/health"
        for i in range(60):
            proc = self.procs.get(tid)
            if proc is not None and proc.returncode is not None:
                raise TenantError("Процесс магазина завершился сразу. Смотри child.log в папке магазина")
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        return
            except Exception:
                pass
            job["percent"] = min(94, 58 + i)
            if i % 10 == 9:
                log(f"Ждём запуска магазина… {i + 1} сек")
            await asyncio.sleep(1)
        raise TenantError("Магазин не запустился за 60 секунд")

    # ── процессы дочерних магазинов ─────────────────────────────────────────
    def _logo_path(self, t) -> str:
        """Путь к логотипу; если запись устарела после восстановления — ищем файл в папке магазина."""
        path = t["logo_path"] or ""
        if path and os.path.isfile(path):
            return path
        found = sorted((self.dir / str(t["id"])).glob("logo.*"))
        return str(found[0]) if found else ""

    def _child_env(self, t) -> dict:
        db = json.loads(self._dec(t["db_enc"]))
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ") if k in os.environ}
        env.update({
            "PYTHONUNBUFFERED": "1",
            "WAIP_ROLE": "child",
            "TENANT_ID": str(t["id"]),
            "BOT_TOKEN": self._dec(t["token_enc"]),
            "OWNER_ID": str(t["admin_id"]),
            "SHOP_TITLE": t["title"],
            "SHOP_THEME": t["theme"],
            "SHOP_LOGO_PATH": self._logo_path(t),
            "GAME_DB_HOST": db["host"],
            "GAME_DB_PORT": str(db["port"]),
            "GAME_DB_USER": db["user"],
            "GAME_DB_PASSWORD": db["password"],
            "GAME_DB_NAME": db["name"],
            "PASSWORD_HASH": db["hash"],
            "WEBAPP_URL": f"{self.public_base}/t/{t['id']}/webapp/",
            "WEBAPP_HOST": "127.0.0.1",
            "WEBAPP_PORT": str(t["port"]),
            "DB_PATH": str(self.dir / str(t["id"]) / "orders.sqlite3"),
            "TENANT_REPORT_URL": f"http://127.0.0.1:{self.master_port}/internal/sale",
            "TENANT_SECRET": self._dec(t["secret_enc"]),
        })
        for k in ("MONEY_PER_STAR", "MONEY_MIN_UNITS"):
            if k in os.environ:
                env[k] = os.environ[k]
        return env

    async def _spawn(self, tid: int) -> asyncio.subprocess.Process:
        t = self._row(tid)
        base = Path(__file__).resolve().parent
        tdir = self.dir / str(tid)
        tdir.mkdir(parents=True, exist_ok=True)  # папка могла исчезнуть (диск очищен) — создаём заново
        log_file = open(tdir / "child.log", "ab")
        try:
            return await asyncio.create_subprocess_exec(
                sys.executable, "main.py", cwd=str(base), env=self._child_env(t),
                stdout=log_file, stderr=asyncio.subprocess.STDOUT,
            )
        finally:
            log_file.close()

    async def _start(self, tid: int) -> None:
        self.db.execute("UPDATE tenants SET status='active' WHERE id=?", (tid,))
        self.db.commit()
        if tid in self.supervisors and not self.supervisors[tid].done():
            return
        proc = await self._spawn(tid)
        self.procs[tid] = proc
        self.supervisors[tid] = asyncio.create_task(self._supervise(tid, proc))

    async def _supervise(self, tid: int, proc) -> None:
        fails = 0
        while True:
            started = time.time()
            rc = await proc.wait()
            self.procs.pop(tid, None)
            row = self._row(tid)
            if not row or row["status"] != "active":
                break
            fails = 0 if time.time() - started > 120 else fails + 1
            logging.warning("tenant %s exited rc=%s, fails=%s", tid, rc, fails)
            if fails >= 5:
                self.db.execute("UPDATE tenants SET status='stopped' WHERE id=?", (tid,))
                self.db.commit()
                logging.error("tenant %s stopped after repeated crashes", tid)
                break
            await asyncio.sleep(min(60, 5 * fails))
            if not self._row(tid) or self._row(tid)["status"] != "active":
                break
            proc = await self._spawn(tid)
            self.procs[tid] = proc
        self.supervisors.pop(tid, None)

    async def _stop(self, tid: int, status: str) -> None:
        self.db.execute("UPDATE tenants SET status=? WHERE id=?", (status, tid))
        self.db.commit()
        proc = self.procs.pop(tid, None)
        if proc and proc.returncode is None:
            proc.terminate()

    async def _remove(self, tid: int) -> None:
        await self._stop(tid, "removed")
        self.db.execute("DELETE FROM tenants WHERE id=?", (tid,))
        self.db.commit()

    async def start_all(self) -> None:
        for row in self.db.execute("SELECT id FROM tenants WHERE status='active'").fetchall():
            try:
                await self._start(int(row["id"]))
            except Exception:
                logging.exception("start tenant %s", row["id"])

    def running_count(self) -> int:
        return sum(1 for p in self.procs.values() if p.returncode is None)

    # ── прокси WebApp дочернего магазина ────────────────────────────────────
    async def proxy(self, request: web.Request) -> web.StreamResponse:
        try:
            tid = int(request.match_info["tid"])
        except ValueError:
            raise web.HTTPNotFound()
        tail = request.match_info.get("tail", "")
        t = self._row(tid)
        if not t or t["status"] != "active":
            return web.json_response({"error": "Магазин сейчас не работает"}, status=503)
        url = f"http://127.0.0.1:{t['port']}/{tail}"
        if request.query_string:
            url += "?" + request.query_string
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() in ("content-type", "x-telegram-init-data", "accept")}
        body = await request.read()
        try:
            async with self.session.request(
                request.method, url, data=body or None, headers=headers,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as resp:
                data = await resp.read()
                return web.Response(
                    body=data, status=resp.status,
                    headers={"Content-Type": resp.headers.get("Content-Type", "application/octet-stream"),
                             "Cache-Control": "no-cache"},
                )
        except aiohttp.ClientError:
            return web.json_response({"error": "Магазин не отвечает, попробуй позже"}, status=502)

    # ── отчёты о продажах и комиссия ────────────────────────────────────────
    def record_sale(self, tid: int, secret: str, external_id: str, stars: int) -> None:
        t = self._row(tid)
        if not t or not secret or not hmac.compare_digest(self._dec(t["secret_enc"]), secret):
            raise TenantError("forbidden")
        stars = int(stars)
        if stars <= 0:
            raise TenantError("bad amount")
        self.db.execute(
            "INSERT OR IGNORE INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)",
            (tid, str(external_id), stars, now_iso()),
        )
        self.db.commit()

    def create_due_commissions(self, now: datetime | None = None) -> list[dict]:
        """Комиссия за последнюю полную неделю (пн–вс UTC). Нет выручки → нет счёта."""
        now = now or now_dt()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        monday = today - timedelta(days=today.weekday())
        start, end = monday - timedelta(days=7), monday
        a, b = start.strftime(TIME_FMT), end.strftime(TIME_FMT)
        created = []
        for t in self.db.execute("SELECT id, admin_id, title FROM tenants WHERE status='active'").fetchall():
            revenue = self.db.execute(
                "SELECT COALESCE(SUM(stars),0) n FROM sales WHERE tenant_id=? AND created_at>=? AND created_at<?",
                (t["id"], a, b),
            ).fetchone()["n"]
            if not revenue:
                continue
            due = (int(revenue) * COMMISSION_PERCENT + 99) // 100  # округление вверх
            payload = "waip_fee_" + secrets.token_urlsafe(12)
            cur = self.db.execute(
                "INSERT OR IGNORE INTO commissions(tenant_id,period_start,period_end,revenue_stars,"
                "due_stars,payload,created_at) VALUES(?,?,?,?,?,?,?)",
                (t["id"], a, b, int(revenue), due, payload, now_iso()),
            )
            if cur.rowcount:
                created.append({"tenant_id": t["id"], "admin_id": t["admin_id"], "title": t["title"],
                                "payload": payload, "due": due, "revenue": int(revenue)})
        self.db.commit()
        return created

    def overdue_tenants(self) -> list[int]:
        cutoff = (now_dt() - timedelta(days=GRACE_DAYS)).strftime(TIME_FMT)
        rows = self.db.execute(
            "SELECT DISTINCT c.tenant_id FROM commissions c JOIN tenants t ON t.id=c.tenant_id "
            "WHERE c.status='due' AND c.created_at<? AND t.status='active'", (cutoff,)
        ).fetchall()
        return [int(r["tenant_id"]) for r in rows]

    def fee_payable(self, payload: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM commissions WHERE payload=? AND status='due'", (payload,)
        ).fetchone() is not None

    async def fee_paid(self, payload: str) -> int | None:
        """Вызывается после successful_payment. Возвращает tenant_id, если комиссия закрыта."""
        row = self.db.execute(
            "SELECT id, tenant_id FROM commissions WHERE payload=? AND status='due'", (payload,)
        ).fetchone()
        if not row:
            return None
        self.db.execute("UPDATE commissions SET status='paid', paid_at=? WHERE id=?", (now_iso(), row["id"]))
        self.db.commit()
        tid = int(row["tenant_id"])
        left = self.db.execute(
            "SELECT COUNT(*) n FROM commissions WHERE tenant_id=? AND status='due'", (tid,)
        ).fetchone()["n"]
        if left == 0:
            await self._start(tid)
        return tid

    async def send_invoice(self, c: dict) -> None:
        from aiogram.types import LabeledPrice  # noqa: WPS433
        if not self.bot:
            return
        try:
            link = await self.bot.create_invoice_link(
                title="Комиссия WAIP SHOP",
                description=f"{COMMISSION_PERCENT}% от выручки за неделю"[:255],
                payload=c["payload"],
                currency="XTR",
                prices=[LabeledPrice(label="Комиссия", amount=int(c["due"]))],
            )
            await self.bot.send_message(
                c["admin_id"],
                f"<b>Комиссия WAIP SHOP — {c['title']}</b>\n"
                f"Выручка за прошлую неделю: <b>{c['revenue']} Stars</b>\n"
                f"Комиссия {COMMISSION_PERCENT}%: <b>{c['due']} Stars</b>\n\n"
                f"Оплати по ссылке до {GRACE_DAYS} дней, иначе магазин приостановится:\n{link}",
            )
        except Exception:
            logging.exception("send fee invoice tenant=%s", c["tenant_id"])

    async def commission_tick(self) -> None:
        for c in self.create_due_commissions():
            await self.send_invoice(c)
        for tid in self.overdue_tenants():
            await self._stop(tid, "suspended")
            logging.warning("tenant %s suspended: commission overdue", tid)
            row = self._row(tid)
            if row and self.bot:
                try:
                    await self.bot.send_message(
                        row["admin_id"],
                        f"Магазин «{row['title']}» приостановлен: не оплачена комиссия. "
                        "Оплати счёт, и магазин запустится автоматически.",
                    )
                except Exception:
                    pass

    async def commission_loop(self) -> None:
        while True:
            try:
                await self.commission_tick()
            except Exception:
                logging.exception("commission_tick")
            await asyncio.sleep(3600)

    async def open_session(self) -> None:
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        for tid in list(self.procs):
            proc = self.procs.get(tid)
            if proc and proc.returncode is None:
                proc.terminate()
        if self.session:
            await self.session.close()
