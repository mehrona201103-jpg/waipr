"""Тесты сайта автодонатов (без Telegram и MySQL, на заглушках)."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet  # noqa: E402

from studio import Studio, check_password, hash_password  # noqa: E402
from tenants import TenantError  # noqa: E402

TOKEN = "123456789:" + "A" * 35


class FakeTenants:
    def __init__(self):
        self.jobs, self.started = {}, []

    async def validate(self, raw):
        return {"ok": True, "checks": [], "bot_username": "client_bot"}

    def start_create(self, owner_id, raw, logo, studio=False):
        assert studio
        self.started.append((owner_id, raw))
        self.jobs["j1"] = {"status": "done", "result": {"id": 7, "bot_username": "client_bot",
                                                        "bot_url": "https://t.me/client_bot"}}
        return "j1"


class StudioTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tn = FakeTenants()
        self.s = Studio(Path(self.tmp), Fernet(Fernet.generate_key()), self.tn, TOKEN,
                        "https://x.example", "boss", "pass12345", 250, 60)
        self.sent = []

        async def fake_send(chat_id, text, **kw):
            self.sent.append((chat_id, text))
        self.s.bot.send_message = fake_send

        async def fake_invoice(**kw):
            self.sent.append(("invoice", kw["payload"]))
        self.s.bot.send_invoice = fake_invoice
        self.s.db.execute("INSERT INTO users(login,pass_hash,role,tg_id,created_at) VALUES('mod1',?,'moderator',555,'x')",
                          (hash_password("modpass"),))
        self.s.db.commit()
        self.form = {"title": "Mega RP", "theme": "#8b5cf6", "token": TOKEN, "admin_id": "999",
                     "db_host": "1.2.3.4", "db_port": "3306", "db_user": "u", "db_password": "p", "db_name": "db1"}

    def user(self, login):
        return self.s.db.execute("SELECT * FROM users WHERE login=?", (login,)).fetchone()

    def test_password(self):
        h = hash_password("abc")
        self.assertTrue(check_password("abc", h))
        self.assertFalse(check_password("abd", h))

    async def test_moderator_split_60_40(self):
        o = await self.s.create_order(self.user("mod1"), self.form)
        self.assertEqual((o["mod_share"], o["bot_share"]), (150, 100))
        self.assertTrue(o["invoice_sent"])
        code = self.s.db.execute("SELECT code FROM orders").fetchone()["code"]
        await self.s._on_payment(code, 250, "XTR", "chg1", 999)
        await asyncio.sleep(0.1)
        self.assertEqual(self.s.owed(self.user("mod1")["id"]), 150)
        self.assertEqual(self.tn.started[0][0], 999)
        row = self.s.db.execute("SELECT * FROM orders").fetchone()
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["fields_enc"], "")  # секреты клиента стёрты
        self.assertTrue(any(c == 555 and "150" in t for c, t in self.sent))

    async def test_payment_is_idempotent_and_checks_amount(self):
        await self.s.create_order(self.user("mod1"), self.form)
        code = self.s.db.execute("SELECT code FROM orders").fetchone()["code"]
        await self.s._on_payment(code, 100, "XTR", "c0", 999)   # мало
        self.assertEqual(len(self.tn.started), 0)
        await self.s._on_payment(code, 250, "XTR", "c1", 999)
        await self.s._on_payment(code, 250, "XTR", "c1", 999)   # повтор
        self.assertEqual(len(self.tn.started), 1)
        self.assertEqual(self.s.owed(self.user("mod1")["id"]), 150)

    async def test_admin_order_gets_no_percent(self):
        o = await self.s.create_order(self.user("boss"), self.form)
        self.assertEqual((o["mod_share"], o["bot_share"]), (0, 250))
        code = self.s.db.execute("SELECT code FROM orders").fetchone()["code"]
        await self.s._on_payment(code, 250, "XTR", "c2", 999)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) n FROM ledger").fetchone()["n"], 0)

    async def test_bad_form_rejected(self):
        bad = dict(self.form, token="oops")
        with self.assertRaises(TenantError):
            await self.s.create_order(self.user("mod1"), bad)


if __name__ == "__main__":
    unittest.main()
