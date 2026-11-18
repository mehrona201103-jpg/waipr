"""WAIP SHOP MASTER control-plane storage.

This module provides tenant registration, revenue ledger, weekly 20% commission
calculation and invoice creation primitives. A child bot must report each paid
sale with record_sale(); the MASTER must run settle_due_periods() and process
successful XTR payments with mark_commission_paid().
"""
from __future__ import annotations
import sqlite3, secrets, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

class MasterStore:
    def __init__(self, path: str = "master.sqlite3"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tenants(
          id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER UNIQUE NOT NULL,
          username TEXT NOT NULL, token_hash TEXT NOT NULL, token_enc TEXT NOT NULL,
          title TEXT NOT NULL, admin_id INTEGER NOT NULL, theme TEXT NOT NULL,
          sticker_file_id TEXT, status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL, last_paid_period TEXT
        );
        CREATE TABLE IF NOT EXISTS sales(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL,
          external_order_id TEXT NOT NULL, stars INTEGER NOT NULL CHECK(stars>0),
          created_at TEXT NOT NULL, UNIQUE(tenant_id, external_order_id),
          FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        );
        CREATE TABLE IF NOT EXISTS commissions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL,
          period_start TEXT NOT NULL, period_end TEXT NOT NULL,
          revenue_stars INTEGER NOT NULL, due_stars INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'due', invoice_payload TEXT UNIQUE,
          invoice_url TEXT, created_at TEXT NOT NULL, paid_at TEXT,
          UNIQUE(tenant_id, period_start, period_end),
          FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        );
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER,
          event TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL
        );
        ''')
        self.db.commit()

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat()

    def add_tenant(self, bot_id: int, username: str, token: str, title: str,
                   admin_id: int, theme: str, sticker_file_id: str = "") -> int:
        if not token or not title.strip() or admin_id <= 0:
            raise ValueError('Нужны токен, название и корректный Telegram ID администратора')
        if not __import__('re').fullmatch(r'#[0-9a-fA-F]{6}', theme):
            raise ValueError('Цвет должен быть в формате #RRGGBB')
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        # Token encryption is delegated to host secret storage; never store plaintext here.
        # token_enc stores an encrypted value only when WAIP_MASTER_FERNET_KEY is configured.
        from cryptography.fernet import Fernet
        import os
        key = os.getenv('WAIP_MASTER_FERNET_KEY', '')
        if not key: raise RuntimeError('Задайте WAIP_MASTER_FERNET_KEY для безопасного хранения токенов')
        encrypted = Fernet(key.encode()).encrypt(token.encode()).decode()
        cur = self.db.execute('''INSERT INTO tenants
          (bot_id,username,token_hash,token_enc,title,admin_id,theme,sticker_file_id,created_at)
          VALUES(?,?,?,?,?,?,?,?,?)''',
          (bot_id, username, token_hash, encrypted, title.strip(), admin_id, theme.lower(), sticker_file_id, self.now()))
        self.db.commit(); return int(cur.lastrowid)

    def record_sale(self, tenant_id: int, external_order_id: str, stars: int):
        if stars <= 0: raise ValueError('Сумма продажи должна быть положительной')
        self.db.execute('INSERT OR IGNORE INTO sales(tenant_id,external_order_id,stars,created_at) VALUES(?,?,?,?)',
                        (tenant_id, str(external_order_id), int(stars), self.now()))
        self.db.commit()

    def create_due_commissions(self, now: datetime | None = None):
        """Create weekly invoices only for completed seven-day periods."""
        now = now or datetime.now(timezone.utc)
        end = now - timedelta(days=7)
        end = end.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=7)
        a, b = start.isoformat(), end.isoformat()
        tenants = self.db.execute("SELECT id FROM tenants WHERE status='active'").fetchall()
        made=[]
        for t in tenants:
            revenue = self.db.execute('SELECT COALESCE(SUM(stars),0) n FROM sales WHERE tenant_id=? AND created_at>=? AND created_at<?', (t['id'],a,b)).fetchone()['n']
            if not revenue: continue
            due = (int(revenue)*20 + 99)//100
            payload = 'waip_fee_' + secrets.token_urlsafe(18)
            cur=self.db.execute('''INSERT OR IGNORE INTO commissions
              (tenant_id,period_start,period_end,revenue_stars,due_stars,invoice_payload,created_at)
              VALUES(?,?,?,?,?,?,?)''',(t['id'],a,b,int(revenue),due,payload,self.now()))
            if cur.rowcount: made.append((int(t['id']),payload,int(due),int(revenue)))
        self.db.commit(); return made

    def set_invoice_url(self, payload: str, url: str):
        self.db.execute("UPDATE commissions SET invoice_url=? WHERE invoice_payload=? AND status='due'",(url,payload)); self.db.commit()

    def mark_commission_paid(self, payload: str):
        row=self.db.execute("SELECT id,tenant_id FROM commissions WHERE invoice_payload=? AND status='due'",(payload,)).fetchone()
        if not row: return False
        self.db.execute("UPDATE commissions SET status='paid',paid_at=? WHERE id=?",(self.now(),row['id']))
        self.db.commit(); return True

    def suspend_overdue(self, grace_days: int = 3):
        cutoff=(datetime.now(timezone.utc)-timedelta(days=grace_days)).isoformat()
        rows=self.db.execute("SELECT DISTINCT tenant_id FROM commissions WHERE status='due' AND created_at<?",(cutoff,)).fetchall()
        ids=[int(r['tenant_id']) for r in rows]
        for tid in ids: self.db.execute("UPDATE tenants SET status='suspended' WHERE id=?",(tid,))
        self.db.commit(); return ids

    def reactivate_if_paid(self, tenant_id: int):
        due=self.db.execute("SELECT COUNT(*) n FROM commissions WHERE tenant_id=? AND status='due'",(tenant_id,)).fetchone()['n']
        if not due:
            self.db.execute("UPDATE tenants SET status='active' WHERE id=?",(tenant_id,)); self.db.commit()
