"""Тесты чистой логики. aiogram/aiohttp/mysql-connector в этой среде недоступны,
поэтому подменяем их заглушками; реальные Telegram/MySQL здесь не вызываются."""
import base64, hashlib, json, os, sys, tempfile, unittest
from datetime import datetime, timezone
from unittest import mock

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

TMP = tempfile.mkdtemp()
os.environ.update({
    "BOT_TOKEN": "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "OWNER_ID": "42", "WAIP_ROLE": "master", "DB_PATH": os.path.join(TMP, "orders.sqlite3"),
    "WEBAPP_URL": "https://shop.example.com/webapp/", "TENANTS_DIR": os.path.join(TMP, "tenants"),
    "GAME_DB_HOST": "10.0.0.5", "GAME_DB_USER": "game", "GAME_DB_PASSWORD": "s3cret", "GAME_DB_NAME": "game",
})

# заглушки внешних библиотек
for name in ["aiogram", "aiogram.enums", "aiogram.client", "aiogram.client.default", "aiogram.filters",
             "aiogram.types", "aiohttp", "mysql", "mysql.connector"]:
    sys.modules.setdefault(name, mock.MagicMock())
class _ClientError(Exception): pass
sys.modules["aiohttp"].ClientError = _ClientError

from cryptography.fernet import Fernet
os.environ["WAIP_MASTER_FERNET_KEY"] = Fernet.generate_key().decode()

import config as C
import tenants as T
import main as M   # модуль поднимает хранилище заказов (sqlite) и Dispatcher-заглушку


class PricingTests(unittest.TestCase):
    def test_all_prices_are_stars_integers(self):
        for code, p in M.PRODUCTS.items():
            self.assertIsInstance(p["price"], int, code)
            self.assertGreaterEqual(p["price"], 50, code)

    def test_stars_for_units_rate(self):
        self.assertEqual(M.stars_for_units(100_000), 50)       # минимум = 50 Stars
        self.assertEqual(M.stars_for_units(2_000), 1)
        self.assertEqual(M.stars_for_units(2_001), 2)          # округление вверх
        self.assertEqual(M.stars_for_units(1_000_000), 500)

    def test_money_item_price_is_stars(self):
        item = M.money_item(100_000)
        self.assertEqual(item["price"], 50)
        self.assertEqual(item["title"], "100 000 игровой валюты")

    def test_no_usd_in_public_strings(self):
        with open(os.path.join(PROJECT, "webapp/app.js"), encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(PROJECT, "webapp/index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("USD", js)
        self.assertNotIn("USD", html)


class PasswordTests(unittest.TestCase):
    def test_gen_password_shape(self):
        for _ in range(1000):
            p = M.gen_password(8)
            self.assertEqual(len(p), 8)
            self.assertTrue(any(c.isdigit() for c in p))
            self.assertTrue(any(c.isalpha() for c in p))

    def test_hash_methods(self):
        raw = "Pass1234"
        with mock.patch.object(M, "PASSWORD_HASH", "md5"):
            self.assertEqual(M.hash_password(raw), hashlib.md5(raw.encode()).hexdigest())
        with mock.patch.object(M, "PASSWORD_HASH", "sha1"):
            self.assertEqual(M.hash_password(raw), hashlib.sha1(raw.encode()).hexdigest())
        with mock.patch.object(M, "PASSWORD_HASH", "plain"):
            self.assertEqual(M.hash_password(raw), raw)


class GameLoginTests(unittest.TestCase):
    def _cfg(self):
        return M.GameConfig(host="h", port=3306, user="u", password="p", database="d", table="accounts",
                            nickname_col="name", level_col="level", admin_col="admin", money_col="money",
                            skin_col="skin", password_col="password")

    def _mock_rows(self, rows):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = rows
        return conn

    def test_right_password_md5(self):
        stored = hashlib.md5(b"secret1").hexdigest()
        with mock.patch.object(M, "PASSWORD_HASH", "md5"), \
             mock.patch.object(sys.modules["mysql"], "connector", mock.MagicMock(connect=mock.MagicMock(return_value=self._mock_rows([(stored,)])))):
            self.assertEqual(M.check_game_login("Ivan", "secret1", self._cfg()), (True, True))

    def test_wrong_password(self):
        stored = hashlib.md5(b"secret1").hexdigest()
        with mock.patch.object(M, "PASSWORD_HASH", "md5"), \
             mock.patch.object(sys.modules["mysql"], "connector", mock.MagicMock(connect=mock.MagicMock(return_value=self._mock_rows([(stored,)])))):
            self.assertEqual(M.check_game_login("Ivan", "wrong", self._cfg()), (True, False))

    def test_account_missing(self):
        with mock.patch.object(sys.modules["mysql"], "connector", mock.MagicMock(connect=mock.MagicMock(return_value=self._mock_rows([])))):
            self.assertEqual(M.check_game_login("Nobody", "x", self._cfg()), (False, False))

    def test_duplicate_nick_blocks(self):
        with mock.patch.object(sys.modules["mysql"], "connector", mock.MagicMock(connect=mock.MagicMock(return_value=self._mock_rows([("a",), ("b",)])))):
            with self.assertRaises(M.GrantError):
                M.check_game_login("Dup", "x", self._cfg())


class CommissionTests(unittest.TestCase):
    def setUp(self):
        self.mgr = T.TenantManager(os.path.join(TMP, "mgr%d" % id(self)), "https://shop.example.com", 10000,
                                   Fernet.generate_key().decode(), master_bot_id=111111)
        self.mgr.session = mock.MagicMock()
        self.spawn = mock.patch.object(self.mgr, "_spawn", mock.AsyncMock(return_value=mock.MagicMock(returncode=None))).start()
        mock.patch.object(self.mgr, "_supervise", mock.AsyncMock()).start()
        self.addCleanup(mock.patch.stopall)
        # создаём магазин напрямую в БД (без реального запуска процесса)
        self.tid = self.mgr.db.execute(
            "INSERT INTO tenants(owner_id,bot_id,bot_username,title,theme,admin_id,token_enc,secret_enc,db_enc,port,created_at)"
            " VALUES(7,555,'child_bot','Мой','#a855f7',7,?,?,?,18001,?)",
            (self.mgr._enc("555:TOKEN"), self.mgr._enc("sec"), self.mgr._enc(json.dumps({"host": "h", "port": 3306,
             "user": "u", "password": "p", "name": "d", "hash": "md5"})), "2026-01-01T00:00:00"),
        ).lastrowid
        self.mgr.db.commit()

    def _week_ago(self):
        # прошлая полная неделя относительно текущей даты
        return datetime(2026, 11, 18, 12, tzinfo=timezone.utc)  # среда

    def test_no_revenue_no_commission(self):
        self.assertEqual(self.mgr.create_due_commissions(self._week_ago()), [])

    def test_twenty_percent_rounded_up(self):
        mid = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)   # внутри недели 9–15 ноября
        self.mgr.db.execute("INSERT INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)",
                            (self.tid, "1", 101, mid.strftime(T.TIME_FMT)))
        self.mgr.db.commit()
        made = self.mgr.create_due_commissions(self._week_ago())
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["revenue"], 101)
        self.assertEqual(made[0]["due"], 21)  # ceil(101 * 0.2) = 21

    def test_commission_created_once_per_week(self):
        mid = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)
        self.mgr.db.execute("INSERT INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)",
                            (self.tid, "2", 500, mid.strftime(T.TIME_FMT)))
        self.mgr.db.commit()
        self.assertEqual(len(self.mgr.create_due_commissions(self._week_ago())), 1)
        self.assertEqual(self.mgr.create_due_commissions(self._week_ago()), [])

    def test_sales_outside_week_ignored(self):
        old = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.mgr.db.execute("INSERT INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)",
                            (self.tid, "3", 900, old.strftime(T.TIME_FMT)))
        self.mgr.db.commit()
        self.assertEqual(self.mgr.create_due_commissions(self._week_ago()), [])

    def test_sale_report_requires_secret_and_is_idempotent(self):
        self.mgr.record_sale(self.tid, "sec", "order-9", 75)
        self.mgr.record_sale(self.tid, "sec", "order-9", 75)   # повтор не дублирует
        n = self.mgr.db.execute("SELECT COUNT(*) n FROM sales WHERE tenant_id=?", (self.tid,)).fetchone()["n"]
        self.assertEqual(n, 1)
        with self.assertRaises(T.TenantError):
            self.mgr.record_sale(self.tid, "wrong", "order-10", 75)
        with self.assertRaises(T.TenantError):
            self.mgr.record_sale(self.tid, "sec", "order-11", 0)

    def test_fee_payment_marks_paid(self):
        import asyncio
        mid = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)
        self.mgr.db.execute("INSERT INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)",
                            (self.tid, "4", 300, mid.strftime(T.TIME_FMT)))
        self.mgr.db.commit()
        c = self.mgr.create_due_commissions(self._week_ago())[0]
        self.assertTrue(self.mgr.fee_payable(c["payload"]))
        asyncio.run(self.mgr.fee_paid(c["payload"]))
        self.assertFalse(self.mgr.fee_payable(c["payload"]))   # второй раз уже не принимаем

    def test_overdue_detection(self):
        mid = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)
        self.mgr.db.execute("INSERT INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)",
                            (self.tid, "5", 300, mid.strftime(T.TIME_FMT)))
        self.mgr.db.commit()
        self.mgr.create_due_commissions(self._week_ago())
        self.assertEqual(self.mgr.overdue_tenants(), [])   # ещё не просрочено
        self.mgr.db.execute("UPDATE commissions SET created_at='2026-01-01T00:00:00'")
        self.mgr.db.commit()
        self.assertEqual(self.mgr.overdue_tenants(), [self.tid])


class TenantFormTests(unittest.TestCase):
    GOOD = {
        "title": "Мой сервер", "theme": "#FF5500", "token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd_-",
        "admin_id": "99887766", "db_host": "db.example.com", "db_port": "3306", "db_user": "u",
        "db_password": "pw", "db_name": "game_db", "hash_method": "md5", "agree": True,
    }

    def test_good_form(self):
        d = T.normalize_fields(self.GOOD)
        self.assertEqual(d["theme"], "#ff5500")
        self.assertEqual(d["admin_id"], 99887766)
        self.assertEqual(d["db"]["port"], 3306)

    def test_bad_inputs(self):
        cases = {
            "title": "", "theme": "red", "token": "not-a-token", "admin_id": "abc",
            "db_host": "bad host!", "db_port": "99999", "db_name": "a-b", "hash_method": "bcrypt",
            "db_password": "",
        }
        for field, value in cases.items():
            bad = dict(self.GOOD, **{field: value})
            with self.assertRaises(T.TenantError, msg=field):
                T.normalize_fields(bad)

    def test_logo_magic_bytes(self):
        self.assertEqual(T.logo_extension(b"\x89PNG\r\n\x1a\n" + b"0" * 20), "png")
        self.assertEqual(T.logo_extension(b"\xff\xd8\xff\xe0" + b"0" * 20), "jpg")
        self.assertEqual(T.logo_extension(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 10), "webp")
        self.assertIsNone(T.logo_extension(b"<svg onload=alert(1)>"))

    def test_child_env_has_no_master_secrets(self):
        mgr = T.TenantManager(os.path.join(TMP, "envtest"), "https://shop.example.com", 10000,
                              Fernet.generate_key().decode(), master_bot_id=1)
        tid = mgr.db.execute(
            "INSERT INTO tenants(owner_id,bot_id,bot_username,title,theme,admin_id,token_enc,secret_enc,db_enc,port,created_at)"
            " VALUES(7,777,'b','Shop','#a855f7',7,?,?,?,18050,'2026-01-01T00:00:00')",
            (mgr._enc("777:TOK"), mgr._enc("reportsecret"), mgr._enc(json.dumps({"host": "dbh", "port": 3306,
             "user": "dbu", "password": "dbp", "name": "dbn", "hash": "sha1"}))),
        ).lastrowid
        mgr.db.commit()
        env = mgr._child_env(mgr._row(tid))
        self.assertEqual(env["WAIP_ROLE"], "child")
        self.assertEqual(env["BOT_TOKEN"], "777:TOK")
        self.assertEqual(env["WEBAPP_URL"], f"https://shop.example.com/t/{tid}/webapp/")
        self.assertEqual(env["WEBAPP_PORT"], "18050")
        self.assertEqual(env["PASSWORD_HASH"], "sha1")
        self.assertNotIn("WAIP_MASTER_FERNET_KEY", env)
        self.assertNotIn(os.environ["BOT_TOKEN"], env.values())   # токен мастера не передаётся
        self.assertNotIn(os.environ["GAME_DB_PASSWORD"], env.values())

    def test_validate_path_rejects_master_token(self):
        import asyncio
        mgr = T.TenantManager(os.path.join(TMP, "valtest"), "https://x", 10000,
                              Fernet.generate_key().decode(), master_bot_id=123456789)
        with mock.patch.object(T, "get_me", return_value={"id": 123456789, "username": "master"}):
            res = asyncio.run(mgr.validate(dict(self.GOOD)))
        self.assertFalse(res["ok"])
        self.assertIn("основного бота", res["checks"][0]["msg"])

    def test_start_create_requires_agreement(self):
        mgr = T.TenantManager(os.path.join(TMP, "agree"), "https://x", 10000,
                              Fernet.generate_key().decode(), master_bot_id=1)
        mgr.session = object()
        bad = dict(self.GOOD, agree=False)
        with self.assertRaises(T.TenantError):
            mgr.start_create(7, bad, None)


class JobFlowTests(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    FORM = dict(TenantFormTests.GOOD, db_password="Zq9-CorrectHorse-Battery")  # длинный, чтобы не совпасть с шифротекстом

    def _mgr(self, name):
        mgr = T.TenantManager(os.path.join(TMP, name), "https://shop.example.com", 10000,
                              Fernet.generate_key().decode(), master_bot_id=111111)
        mgr.session = mock.MagicMock()
        mock.patch.object(mgr, "_spawn", mock.AsyncMock(return_value=mock.MagicMock(returncode=None))).start()
        mock.patch.object(mgr, "_supervise", mock.AsyncMock()).start()
        mock.patch.object(mgr, "_wait_ready", mock.AsyncMock()).start()
        self.addCleanup(mock.patch.stopall)
        return mgr

    async def _wait(self, mgr, job_id, owner=7):
        import asyncio
        for _ in range(1000):
            job = mgr.job_for(job_id, owner)
            if job["status"] != "running":
                return job
            await asyncio.sleep(0.01)
        raise AssertionError("job did not finish")

    def test_success_with_logo_and_progress(self):
        import asyncio
        mgr = self._mgr("job_ok")
        logo_b64 = "data:image/png;base64," + base64.b64encode(self.PNG).decode()
        async def run():
            with mock.patch.object(T, "get_me", return_value={"id": 555, "username": "newbot"}), \
                 mock.patch.object(T, "set_bot_name", return_value=True), \
                 mock.patch.object(T, "mysql_check", return_value=(True, ["MySQL: ok"])):
                jid = mgr.start_create(7, dict(self.FORM, agree=True), logo_b64)
                return await self._wait(mgr, jid)
        job = asyncio.run(run())
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["percent"], 100)
        self.assertTrue(job["result"]["webapp_url"].endswith("/webapp/"))
        self.assertIn("/t/", job["result"]["webapp_url"])
        tid = job["result"]["id"]
        self.assertTrue((mgr.dir / str(tid) / "logo.png").exists())
        row = mgr._row(tid)
        self.assertEqual(row["port"], 18000 + tid)
        self.assertEqual(row["bot_username"], "newbot")
        # в логах нет токена и пароля
        text = "\n".join(job["logs"])
        self.assertNotIn(self.FORM["token"], text)
        self.assertNotIn(self.FORM["db_password"], text)
        # токен и пароль в БД зашифрованы
        self.assertNotIn(self.FORM["token"], row["token_enc"])
        self.assertNotIn(self.FORM["db_password"], row["db_enc"])

    def test_mysql_failure_rolls_back(self):
        import asyncio
        mgr = self._mgr("job_fail")
        async def run():
            with mock.patch.object(T, "get_me", return_value={"id": 556, "username": "bad"}), \
                 mock.patch.object(T, "set_bot_name", return_value=True), \
                 mock.patch.object(T, "mysql_check", return_value=(False, ["Нет колонок: password"])):
                jid = mgr.start_create(7, dict(self.FORM), None)
                return await self._wait(mgr, jid)
        job = asyncio.run(run())
        self.assertEqual(job["status"], "failed")
        self.assertIn("MySQL", job["error"])
        self.assertIsNone(mgr.db.execute("SELECT 1 FROM tenants WHERE bot_id=556").fetchone())

    def test_other_user_cannot_read_job(self):
        import asyncio
        mgr = self._mgr("job_owner")
        async def run():
            with mock.patch.object(T, "get_me", return_value={"id": 557, "username": "x"}), \
                 mock.patch.object(T, "set_bot_name", return_value=True), \
                 mock.patch.object(T, "mysql_check", return_value=(False, ["fail"])):
                jid = mgr.start_create(7, dict(self.FORM), None)
                await self._wait(mgr, jid)
                return jid
        jid = asyncio.run(run())
        self.assertIsNone(mgr.job_for(jid, owner_id=999))

    def test_owner_limit(self):
        mgr = self._mgr("limit")
        for i in range(T.MAX_TENANTS_PER_USER):
            mgr.db.execute("INSERT INTO tenants(owner_id,bot_id,bot_username,title,theme,admin_id,token_enc,secret_enc,db_enc,port,created_at)"
                           " VALUES(7,?,'b','t','#a855f7',7,'x','x','x',0,'2026')", (900 + i,))
        mgr.db.commit()
        with self.assertRaises(T.TenantError):
            mgr.start_create(7, dict(self.FORM), None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
