"""Бесплатное хранение данных: резервные копии баз и логотипов в приватный Telegram-канал.

Как работает:
  * Мастер раз в INTERVAL секунд проверяет базы (заказы, магазины, логотипы) и заливает
    изменившиеся файлы в канал BACKUP_CHAT_ID (документы Telegram, без оплаты).
  * Список файлов (ключ → file_id, путь) хранится в одном закреплённом сообщении канала.
  * При старте на пустом диске мастер читает закреплённое сообщение и восстанавливает файлы.

Ограничения: данные могут отстать от реального состояния примерно на INTERVAL секунд.
Канал должен быть ПРИВАТНЫМ: в бэкапах есть заказы и ники игроков.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import tempfile
import urllib.request
from pathlib import Path

INTERVAL = 120
INDEX_PREFIX = "WAIP_BACKUP_INDEX\n"
INDEX_MAX_CHARS = 4000


# ── низкоуровневый доступ к Bot API (для старта, до создания бота) ───────────
def _api(token: str, method: str, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"{method}: {body.get('description')}")
    return body["result"]


def _download(token: str, file_id: str) -> bytes:
    info = _api(token, "getFile", file_id=file_id)
    url = f"https://api.telegram.org/file/bot{token}/{info['file_path']}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def _read_index(token: str, chat_id: int) -> tuple[dict, int | None]:
    chat = _api(token, "getChat", chat_id=chat_id)
    pinned = chat.get("pinned_message") or {}
    text = pinned.get("text") or ""
    if not text.startswith(INDEX_PREFIX):
        return {}, None
    return json.loads(text[len(INDEX_PREFIX):]), pinned.get("message_id")


def restore(token: str, chat_id: int, data_dir: str) -> str:
    """Восстанавливает файлы, которых нет на диске. Возвращает 'ok', 'empty' или 'error'."""
    try:
        index, _ = _read_index(token, chat_id)
    except Exception as exc:
        logging.error("Восстановление из Telegram не удалось: %s. Бэкапы отключены до перезапуска.", exc)
        return "error"
    if not index:
        logging.info("Резервной копии в канале нет — стартуем с чистой базы")
        return "empty"
    for key, entry in index.items():
        target = Path(data_dir) / entry["p"]
        if target.exists():
            continue
        try:
            content = _download(token, entry["f"])
        except Exception as exc:
            logging.error("Не удалось скачать %s из резервной копии: %s. Бэкапы отключены.", key, exc)
            return "error"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        logging.info("Восстановлен файл %s", entry["p"])
    logging.info("Восстановление из резервной копии завершено: %d файлов", len(index))
    return "ok"


def _sqlite_snapshot(src: str, dst: str) -> None:
    """Согласованная копия SQLite (безопасно, даже когда база открыта другим процессом)."""
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


class BackupManager:
    def __init__(self, bot, token: str, chat_id: int, data_dir: str, files_provider):
        self.bot = bot
        self.token = token
        self.chat_id = chat_id
        self.data_dir = Path(data_dir)
        self.files_provider = files_provider  # () -> list[(key, abs_path, rel_path)]
        self.index: dict[str, dict] = {}
        self.sigs: dict[str, tuple] = {}
        self.index_msg_id: int | None = None
        self.enabled = True
        try:
            self.index, self.index_msg_id = _read_index(token, chat_id)
        except Exception:
            logging.exception("Не удалось прочитать индекс бэкапа при старте")

    @staticmethod
    def _sig(path: str):
        if not os.path.exists(path):
            return None
        st = os.stat(path)
        wal = path + "-wal"
        wal_size = os.stat(wal).st_size if os.path.exists(wal) else 0
        return (st.st_mtime_ns, st.st_size, wal_size)

    async def _delete(self, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await self.bot.delete_message(self.chat_id, message_id)
        except Exception:
            pass

    async def _publish_index(self) -> None:
        text = INDEX_PREFIX + json.dumps(self.index, separators=(",", ":"), ensure_ascii=False)
        if len(text) > INDEX_MAX_CHARS:
            logging.error("Индекс бэкапа стал слишком большим (%d симв.). Удали лишние магазины.", len(text))
            return
        msg = await self.bot.send_message(self.chat_id, text, disable_notification=True)
        await self.bot.pin_chat_message(self.chat_id, msg.message_id, disable_notification=True)
        await self._delete(self.index_msg_id)
        self.index_msg_id = msg.message_id

    async def sync(self) -> bool:
        if not self.enabled:
            return False
        changed = False
        files = self.files_provider()
        present = {key for key, _, _ in files}
        # удаляем из бэкапа файлы, которых больше нет (например, удалённый магазин)
        for key in list(self.index):
            if key not in present:
                await self._delete(self.index[key]["m"])
                self.index.pop(key)
                self.sigs.pop(key, None)
                changed = True
        for key, abs_path, rel in files:
            sig = self._sig(abs_path)
            if sig is None:
                continue
            if key in self.index and self.sigs.get(key) == sig:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, os.path.basename(rel))
                if abs_path.endswith(".sqlite3"):
                    _sqlite_snapshot(abs_path, out)
                else:
                    with open(abs_path, "rb") as src, open(out, "wb") as dst:
                        dst.write(src.read())
                from aiogram.types import FSInputFile  # noqa: WPS433
                msg = await self.bot.send_document(
                    self.chat_id, FSInputFile(out, filename=os.path.basename(rel)),
                    disable_notification=True,
                )
            old = self.index.get(key)
            self.index[key] = {"f": msg.document.file_id, "m": msg.message_id, "p": rel}
            self.sigs[key] = sig
            if old:
                await self._delete(old["m"])
            changed = True
        if changed:
            await self._publish_index()
        return changed

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(INTERVAL)
            try:
                await self.sync()
            except Exception:
                logging.exception("Ошибка резервного копирования")

    def install_sigterm_flush(self) -> None:
        """При остановке (SIGTERM) успеваем сохранить последние изменения."""
        loop = asyncio.get_running_loop()

        async def _flush_and_exit():
            try:
                await asyncio.wait_for(self.sync(), 25)
                logging.info("Последние данные сохранены в резервную копию")
            except Exception:
                logging.exception("Не удалось сохранить бэкап при остановке")
            finally:
                os._exit(0)

        loop.add_signal_handler(signal.SIGTERM, lambda: loop.create_task(_flush_and_exit()))
