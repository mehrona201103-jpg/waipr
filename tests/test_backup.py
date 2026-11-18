"""Тесты бэкапа в Telegram. Telegram не вызывается: бот и Bot API подменены."""
import asyncio, json, os, sqlite3, sys, tempfile, unittest
from types import SimpleNamespace
from unittest import mock

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.modules.setdefault("aiogram", mock.MagicMock())
sys.modules.setdefault("aiogram.types", mock.MagicMock())
import backup as B


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, nick TEXT)")
    con.executemany("INSERT INTO orders(nick) VALUES(?)", [(r,) for r in rows])
    con.commit()
    con.close()


class FakeBot:
    def __init__(self):
        self.sent, self.pinned, self.deleted, self.counter = [], [], [], 100

    async def send_document(self, chat_id, doc, disable_notification=True):
        self.counter += 1
        self.sent.append(("doc", os.path.basename(doc.path if hasattr(doc, "path") else "x")))
        return SimpleNamespace(message_id=self.counter, document=SimpleNamespace(file_id=f"F{self.counter}"))

    async def send_message(self, chat_id, text, disable_notification=True):
        self.counter += 1
        self.sent.append(("text", text))
        return SimpleNamespace(message_id=self.counter)

    async def pin_chat_message(self, chat_id, message_id, disable_notification=True):
        self.pinned.append(message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_contains_committed_rows_from_wal_db(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "orders.sqlite3")
        make_db(src, ["a", "b", "c"])
        out = os.path.join(d, "copy.sqlite3")
        B._sqlite_snapshot(src, out)
        con = sqlite3.connect(out)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 3)
        con.close()


class RestoreTests(unittest.TestCase):
    def test_empty_when_no_index(self):
        with mock.patch.object(B, "_read_index", return_value=({}, None)):
            self.assertEqual(B.restore("t", -100, tempfile.mkdtemp()), "empty")

    def test_error_when_telegram_unreachable(self):
        with mock.patch.object(B, "_read_index", side_effect=OSError("no network")):
            self.assertEqual(B.restore("t", -100, tempfile.mkdtemp()), "error")

    def test_restores_missing_files_and_keeps_existing(self):
        d = tempfile.mkdtemp()
        keep = os.path.join(d, "orders.sqlite3")
        with open(keep, "wb") as f:
            f.write(b"LOCAL")
        index = {
            "master_orders": {"f": "F1", "m": 1, "p": "orders.sqlite3"},
            "tenant_3_orders": {"f": "F2", "m": 2, "p": "tenants/3/orders.sqlite3"},
            "tenant_3_logo.png": {"f": "F3", "m": 3, "p": "tenants/3/logo.png"},
        }
        with mock.patch.object(B, "_read_index", return_value=(index, 9)), \
             mock.patch.object(B, "_download", side_effect=lambda tok, fid: fid.encode()):
            self.assertEqual(B.restore("t", -100, d), "ok")
        self.assertEqual(open(keep, "rb").read(), b"LOCAL")          # локальный файл не затёрт
        self.assertEqual(open(os.path.join(d, "tenants/3/logo.png"), "rb").read(), b"F3")

    def test_download_failure_is_error(self):
        index = {"x": {"f": "F9", "m": 1, "p": "a.bin"}}
        with mock.patch.object(B, "_read_index", return_value=(index, 1)), \
             mock.patch.object(B, "_download", side_effect=OSError("boom")):
            self.assertEqual(B.restore("t", -100, tempfile.mkdtemp()), "error")


class SyncTests(unittest.TestCase):
    def _mgr(self, files, index=None):
        bot = FakeBot()
        with mock.patch.object(B, "_read_index", return_value=(index or {}, None)):
            mgr = B.BackupManager(bot, "t", -100, tempfile.mkdtemp(), lambda: files)
        return mgr, bot

    def test_uploads_changed_db_once_and_publishes_index(self):
        d = tempfile.mkdtemp()
        db = os.path.join(d, "orders.sqlite3")
        make_db(db, ["x"])
        mgr, bot = self._mgr([("master_orders", db, "orders.sqlite3")])
        self.assertTrue(asyncio.run(mgr.sync()))
        self.assertEqual(mgr.index["master_orders"]["f"], "F101")
        self.assertEqual(bot.pinned, [102])                  # индекс закреплён
        text = [t for kind, t in bot.sent if kind == "text"][0]
        self.assertTrue(text.startswith(B.INDEX_PREFIX))
        self.assertEqual(json.loads(text[len(B.INDEX_PREFIX):])["master_orders"]["p"], "orders.sqlite3")
        # без изменений повторно не отправляем
        bot.sent.clear()
        self.assertFalse(asyncio.run(mgr.sync()))
        self.assertEqual(bot.sent, [])

    def test_change_triggers_new_upload_and_deletes_old(self):
        d = tempfile.mkdtemp()
        db = os.path.join(d, "orders.sqlite3")
        make_db(db, ["x"])
        mgr, bot = self._mgr([("master_orders", db, "orders.sqlite3")])
        asyncio.run(mgr.sync())
        old_msg = mgr.index["master_orders"]["m"]
        make_db(db, ["y"])  # изменили базу
        os.utime(db, (9_999_999_999, 9_999_999_999))
        bot.sent.clear()
        self.assertTrue(asyncio.run(mgr.sync()))
        self.assertIn(old_msg, bot.deleted)

    def test_deleted_tenant_is_pruned_from_index(self):
        index = {"tenant_9_orders": {"f": "F1", "m": 50, "p": "tenants/9/orders.sqlite3"}}
        mgr, bot = self._mgr([], index=dict(index))
        self.assertTrue(asyncio.run(mgr.sync()))
        self.assertNotIn("tenant_9_orders", mgr.index)
        self.assertIn(50, bot.deleted)

    def test_disabled_manager_does_nothing(self):
        mgr, bot = self._mgr([])
        mgr.enabled = False
        self.assertFalse(asyncio.run(mgr.sync()))
        self.assertEqual(bot.sent, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
