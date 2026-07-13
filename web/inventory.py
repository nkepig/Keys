"""
库存管理 — 单文件 FastAPI + SQLite Web 应用。
运行：python3.13 inventory.py  →  http://localhost:8000
依赖：fastapi uvicorn jinja2 pypinyin python-multipart
"""

import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from jinja2 import Environment
from pypinyin import pinyin, Style
from starlette.middleware.sessions import SessionMiddleware

# ════════════════════════════════════════════════════════════
#  Config
# ════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).parent / "inventory.db"
ALPHABETS: tuple[str, ...] = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ("#",)
TEMPLATES = Environment(autoescape=True)
SHA_TZ = timezone(timedelta(hours=8))

AUTH_USER = "root"
AUTH_PASS = "asd12345"
SESSION_SECRET = os.environ.get("INVENTORY_SESSION_SECRET") or secrets.token_urlsafe(32)


# ════════════════════════════════════════════════════════════
#  Database — fresh schema, immutable history snapshots
# ════════════════════════════════════════════════════════════


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


FRESH_SCHEMA_COLUMNS = {"inventory_items": "unit_cost_cents", "stock_records": "unit_cost_cents"}

SCHEMA_DDL = """
    CREATE TABLE IF NOT EXISTS inventory_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        brand           TEXT NOT NULL,
        model           TEXT NOT NULL,
        quantity        INTEGER NOT NULL DEFAULT 0,
        unit_cost_cents INTEGER NOT NULL DEFAULT 0,
        pinyin_initial  TEXT NOT NULL DEFAULT '#',
        created_at      TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
        UNIQUE(brand, model)
    );
    CREATE TABLE IF NOT EXISTS stock_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id         INTEGER,
        type            TEXT NOT NULL CHECK(type IN ('in','out')),
        quantity        INTEGER NOT NULL,
        unit_cost_cents INTEGER NOT NULL DEFAULT 0,
        brand_snapshot  TEXT NOT NULL,
        model_snapshot  TEXT NOT NULL,
        occurred_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
    );
    CREATE INDEX IF NOT EXISTS idx_sr_time ON stock_records(occurred_at);
    CREATE INDEX IF NOT EXISTS idx_sr_item ON stock_records(item_id);
"""


def _has_column(c: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
        return column in cols
    except sqlite3.OperationalError:
        return False


def init_db() -> None:
    c = _conn()
    stale = False
    for table, required_col in FRESH_SCHEMA_COLUMNS.items():
        exists = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists and not _has_column(c, table, required_col):
            stale = True
            break
    if stale:
        c.executescript("DROP TABLE IF EXISTS inventory_items; DROP TABLE IF EXISTS stock_records;")
    c.executescript(SCHEMA_DDL)
    c.commit()
    c.close()


def get_all_items() -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM inventory_items ORDER BY pinyin_initial, brand, model"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_item(item_id: int) -> Optional[dict]:
    c = _conn()
    row = c.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def add_or_restock(
    brand: str,
    model: str,
    quantity: int,
    pinyin_initial: str,
    price_cents: int | None = None,
) -> dict:
    """Add new item or restock existing. If price_cents is None, preserve existing cost."""
    c = _conn()
    ex = c.execute(
        "SELECT * FROM inventory_items WHERE brand=? AND model=?", (brand, model)
    ).fetchone()
    if ex:
        new_qty = ex["quantity"] + quantity
        resolved_price = price_cents if price_cents is not None else ex["unit_cost_cents"]
        c.execute(
            "UPDATE inventory_items SET quantity=?, unit_cost_cents=?, updated_at=datetime('now','+8 hours') WHERE id=?",
            (new_qty, resolved_price, ex["id"]),
        )
        c.execute(
            "INSERT INTO stock_records (item_id, type, quantity, unit_cost_cents, brand_snapshot, model_snapshot) "
            "VALUES (?, 'in', ?, ?, ?, ?)",
            (ex["id"], quantity, resolved_price, brand, model),
        )
        c.commit()
        item = dict(c.execute(
            "SELECT * FROM inventory_items WHERE id=?", (ex["id"],)
        ).fetchone())
    else:
        cost = price_cents if price_cents is not None else 0
        cur = c.execute(
            "INSERT INTO inventory_items (brand, model, quantity, pinyin_initial, unit_cost_cents) VALUES (?,?,?,?,?)",
            (brand, model, quantity, pinyin_initial, cost),
        )
        iid = cur.lastrowid
        c.execute(
            "INSERT INTO stock_records (item_id, type, quantity, unit_cost_cents, brand_snapshot, model_snapshot) "
            "VALUES (?, 'in', ?, ?, ?, ?)",
            (iid, quantity, cost, brand, model),
        )
        c.commit()
        item = dict(c.execute(
            "SELECT * FROM inventory_items WHERE id=?", (iid,)
        ).fetchone())
    c.close()
    return item


def do_stock_out(item_id: int, quantity: int) -> dict:
    c = _conn()
    item = c.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        c.close()
        raise ValueError("Item not found")
    actual = min(quantity, item["quantity"])
    c.execute(
        "UPDATE inventory_items SET quantity=?, updated_at=datetime('now','+8 hours') WHERE id=?",
        (item["quantity"] - actual, item_id),
    )
    if actual > 0:
        c.execute(
            "INSERT INTO stock_records (item_id, type, quantity, unit_cost_cents, brand_snapshot, model_snapshot) "
            "VALUES (?, 'out', ?, ?, ?, ?)",
            (item_id, actual, item["unit_cost_cents"], item["brand"], item["model"]),
        )
    c.commit()
    result = dict(c.execute(
        "SELECT * FROM inventory_items WHERE id=?", (item_id,)
    ).fetchone())
    c.close()
    return {"item": result, "actual_out": actual}


def delete_item(item_id: int) -> None:
    """Delete item but preserve immutable history snapshots (no cascade)."""
    c = _conn()
    c.execute("DELETE FROM inventory_items WHERE id=?", (item_id,))
    c.commit()
    c.close()


def get_stock_history(start: date, end: date) -> list[dict]:
    """Return records in [start 00:00, end+1day 00:00) half-open range (Asia/Shanghai)."""
    c = _conn()
    start_ts = f"{start.isoformat()} 00:00:00"
    end_ts = f"{(end + timedelta(days=1)).isoformat()} 00:00:00"
    rows = c.execute(
        """SELECT id, item_id, type, quantity, unit_cost_cents, brand_snapshot,
                  model_snapshot, occurred_at
           FROM stock_records
           WHERE occurred_at >= ? AND occurred_at < ?
           ORDER BY occurred_at DESC""",
        (start_ts, end_ts),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def build_csv() -> str:
    lines = ["\ufeff品牌,型号,数量,进价(元)"]
    for item in get_all_items():
        cents = item["unit_cost_cents"]
        lines.append(
            f"{item['brand']},{item['model']},{item['quantity']},{cents // 100}.{cents % 100:02d}"
        )
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════


def parse_price_cents(raw_price: str) -> int | None:
    try:
        price = Decimal(raw_price)
    except InvalidOperation:
        return None
    cents = price * 100
    if not price.is_finite() or price < 0 or cents != cents.to_integral_value():
        return None
    return int(cents)


def safe_next_path(next_path: str | None) -> str:
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


def today_sha() -> date:
    return datetime.now(SHA_TZ).date()


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def pinyin_initial(text: str) -> str:
    if not text:
        return "#"
    result = pinyin(text[0], style=Style.FIRST_LETTER)
    if result and result[0] and result[0][0]:
        letter = result[0][0].upper()
        if letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            return letter
    return "#"


def model_sort_key(model: str) -> tuple[tuple[int, int | str], ...]:
    normalized = "".join(
        syllables[0]
        for syllables in pinyin(model, style=Style.NORMAL, heteronym=False)
    )
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", normalized)
        if part
    )


# ════════════════════════════════════════════════════════════
#  CSS Design System (shared across all pages)
# ════════════════════════════════════════════════════════════

_CSS = r"""
:root{
  --bg-canvas:#F7F8FA; --bg-surface:#FFFFFF; --bg-subtle:#F0F2F5;
  --ink-1:#1A1D23; --ink-2:#5A6172; --ink-3:#8B92A3; --ink-4:#C5CAD6;
  --accent:#00A878; --accent-h:#009168; --accent-bg:#E6F7F1;
  --blue:#3B6EF6; --blue-bg:#EBF1FE;
  --red:#E5484D; --red-bg:#FCE8E8;
  --border:#E8EAEF; --border-s:#D1D5DD;
  --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:24px; --r-full:9999px;
  --sh-sm:0 1px 2px rgba(16,24,40,.04);
  --sh-md:0 4px 12px rgba(16,24,40,.06);
  --sh-lg:0 12px 32px rgba(16,24,40,.08);
  --dur:200ms; --ease:cubic-bezier(.22,.61,.36,1);
  --font:'Inter',system-ui,-apple-system,'PingFang SC','Noto Sans SC',sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0;}
html{font-size:16px;-webkit-text-size-adjust:100%;}
body{font-family:var(--font);background:var(--bg-canvas);color:var(--ink-1);
  line-height:1.5;min-height:100dvh;-webkit-font-smoothing:antialiased;}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer;}
input{font:inherit;color:inherit;}
a{color:inherit;text-decoration:none;}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;}}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);}

/* Header */
.hdr{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.82);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);}
.hdr-in{max-width:720px;margin:0 auto;padding:14px 20px 12px;}
.hdr-row{display:flex;align-items:center;gap:12px;}
.hdr-title{font-size:1.25rem;font-weight:700;color:var(--ink-1);}
.hdr-back{width:36px;height:36px;border-radius:var(--r-full);display:flex;
  align-items:center;justify-content:center;transition:background var(--dur) var(--ease);}
.hdr-back:hover{background:var(--bg-subtle);}
.hdr-back svg{width:20px;height:20px;color:var(--ink-2);}

/* Menu */
.menu-wrap{position:relative;margin-left:auto;}
.menu-btn{width:36px;height:36px;border-radius:var(--r-sm);display:flex;
  align-items:center;justify-content:center;transition:background var(--dur) var(--ease);}
.menu-btn:hover{background:var(--bg-subtle);}
.menu-btn svg{width:22px;height:22px;color:var(--ink-2);}
.menu-panel{position:absolute;right:0;top:calc(100% + 4px);width:168px;
  background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--r-md);
  box-shadow:var(--sh-md);overflow:hidden;z-index:40;
  opacity:0;transform:translateY(-4px);pointer-events:none;
  transition:opacity var(--dur) var(--ease),transform var(--dur) var(--ease);}
.menu-panel.open{opacity:1;transform:translateY(0);pointer-events:auto;}
.menu-panel a,.menu-panel button{display:block;width:100%;text-align:left;
  padding:11px 16px;font-size:.875rem;color:var(--ink-2);
  transition:background var(--dur) var(--ease);min-height:44px;display:flex;align-items:center;}
.menu-panel a:hover,.menu-panel button:hover{background:var(--bg-subtle);}
.menu-panel .divider{border-top:1px solid var(--border);}
.menu-panel .danger{color:var(--red);}
.menu-panel .danger:hover{background:var(--red-bg);}

/* Search */
.search-wrap{position:relative;margin-top:10px;}
.search-icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);
  width:20px;height:20px;color:var(--ink-3);pointer-events:none;}
.search-input{width:100%;height:44px;padding:0 16px 0 40px;
  border-radius:var(--r-md);background:var(--bg-subtle);border:1.5px solid transparent;
  font-size:1rem;color:var(--ink-1);outline:none;
  touch-action:manipulation;-webkit-user-select:text;user-select:text;
  transition:border-color var(--dur) var(--ease),background var(--dur) var(--ease);}
.search-input::placeholder{color:var(--ink-3);}
.search-input:focus{background:var(--bg-surface);border-color:var(--accent);}

/* Main */
.main{max-width:720px;margin:0 auto;padding:8px 20px 120px;}

/* Empty state */
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding-top:140px;text-align:center;}
.empty-circle{width:80px;height:80px;border-radius:var(--r-full);
  background:var(--bg-subtle);display:flex;align-items:center;justify-content:center;margin-bottom:16px;}
.empty-circle svg{width:40px;height:40px;color:var(--ink-4);}
.empty-title{font-size:1rem;color:var(--ink-3);}
.empty-hint{font-size:.8125rem;color:var(--ink-4);margin-top:4px;}

/* Alphabet bar */
.alpha-bar{position:fixed;right:4px;top:50%;transform:translateY(-50%);
  display:flex;flex-direction:column;gap:1px;user-select:none;z-index:20;
  max-height:70vh;overflow-y:auto;scrollbar-width:none;}
.alpha-bar::-webkit-scrollbar{display:none;}
.alpha-link{width:24px;height:20px;display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:600;color:var(--ink-2);border-radius:4px;
  transition:background var(--dur) var(--ease);text-decoration:none;}
.alpha-link:hover{background:var(--bg-subtle);}
.alpha-link.off{color:var(--ink-4);pointer-events:none;}
@media(max-width:380px){.alpha-bar{display:none;}}

/* Brand group */
.brand-group{margin-bottom:8px;background:var(--bg-surface);
  border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden;}
.brand-toggle{width:100%;min-height:52px;padding:12px 16px;display:flex;
  align-items:center;justify-content:flex-start;gap:12px;text-align:left;
  transition:background var(--dur) var(--ease);}
.brand-toggle:hover{background:var(--bg-subtle);}
.brand-info{flex:1;min-width:0;}
.brand-name{display:block;font-weight:600;font-size:.9375rem;color:var(--ink-1);}
.brand-meta{display:block;font-size:.75rem;color:var(--ink-3);margin-top:2px;}
.chevron{width:20px;height:20px;color:var(--ink-3);flex-shrink:0;
  transition:transform var(--dur) var(--ease);}
.chevron.open{transform:rotate(90deg);}
.brand-models{border-top:1px solid var(--border);display:none;}
.brand-models.open{display:block;}

/* Model row */
.model-row{padding:12px 16px;display:flex;align-items:center;gap:12px;
  border-bottom:1px solid var(--border);transition:background var(--dur) var(--ease);}
.model-row:last-child{border-bottom:none;}
.model-row:hover{background:var(--bg-subtle);}
.model-row.empty-stock{opacity:.4;}
.model-info{flex:1;min-width:0;}
.model-name{font-weight:500;font-size:.875rem;color:var(--ink-1);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.model-qty{font-size:.75rem;color:var(--ink-3);margin-top:2px;}
.model-price{font-size:.75rem;color:var(--ink-2);margin-top:1px;}
.model-actions{display:flex;align-items:center;gap:6px;flex-shrink:0;margin-left:8px;}
.icon-btn{width:40px;height:40px;border-radius:var(--r-full);display:flex;
  align-items:center;justify-content:center;transition:all var(--dur) var(--ease);
  border:none;}
.icon-btn svg{width:18px;height:18px;}
.icon-btn.out{background:var(--blue-bg);color:var(--blue);}
.icon-btn.out:hover{background:var(--blue);color:#fff;}
.icon-btn.in{background:var(--accent-bg);color:var(--accent);}
.icon-btn.in:hover{background:var(--accent);color:#fff;}
.icon-btn.del{background:var(--bg-subtle);color:var(--ink-3);}
.icon-btn.del:hover{background:var(--red-bg);color:var(--red);}
.icon-btn.off{opacity:.3;pointer-events:none;}

/* Capsule */
.capsule{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:30;
  display:flex;width:192px;height:56px;border-radius:var(--r-full);
  background:var(--bg-surface);border:1px solid var(--border);
  box-shadow:var(--sh-lg);overflow:hidden;}
.capsule-half{flex:1;display:flex;align-items:center;justify-content:center;gap:6px;
  font-size:.875rem;font-weight:500;color:var(--ink-1);transition:all var(--dur) var(--ease);}
.capsule-half:first-child{background:var(--accent-bg);border-radius:var(--r-full) 0 0 var(--r-full);}
.capsule-half:last-child{background:var(--blue-bg);border-radius:0 var(--r-full) var(--r-full) 0;}
.capsule-half:hover{filter:brightness(.95);}
.capsule-half:active{transform:scale(.96);}
.capsule-half .sign{font-size:1.125rem;font-weight:700;}
.capsule-half:first-child .sign{color:var(--accent);}
.capsule-half:last-child .sign{color:var(--blue);}
.capsule-half.off{opacity:.4;pointer-events:none;}

/* Modal */
.modal-bg{position:fixed;inset:0;z-index:50;background:rgba(16,24,40,.32);
  display:flex;align-items:flex-end;justify-content:center;
  opacity:0;visibility:hidden;pointer-events:none;
  transition:opacity var(--dur) var(--ease),visibility 0s linear var(--dur);}
@media(min-width:640px){.modal-bg{align-items:center;}}
.modal-bg.open{opacity:1;visibility:visible;pointer-events:auto;
  backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);
  transition:opacity var(--dur) var(--ease),visibility 0s;}
.modal-card{width:100%;max-width:420px;background:var(--bg-surface);
  border-radius:var(--r-xl) var(--r-xl) 0 0;padding:24px;
  box-shadow:var(--sh-lg);max-height:85vh;overflow-y:auto;
  position:relative;z-index:1;pointer-events:auto;
  opacity:0;transition:opacity var(--dur) var(--ease);}
.modal-bg.open .modal-card{opacity:1;}
@media(min-width:640px){
  .modal-card{border-radius:var(--r-xl);max-height:none;opacity:1;
    transform:scale(.96);transition:transform var(--dur) var(--ease),opacity var(--dur) var(--ease);}
  .modal-bg.open .modal-card{transform:scale(1);}
}
.modal-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;}
.modal-title{font-size:1.125rem;font-weight:600;color:var(--ink-1);}
.modal-close{width:32px;height:32px;border-radius:var(--r-full);display:flex;
  align-items:center;justify-content:center;transition:background var(--dur) var(--ease);}
.modal-close:hover{background:var(--bg-subtle);}
.modal-close svg{width:20px;height:20px;color:var(--ink-3);}

/* Form */
.field{margin-bottom:16px;}
.field label{display:block;font-size:.8125rem;font-weight:500;color:var(--ink-2);margin-bottom:6px;}
.field input{width:100%;height:48px;padding:0 16px;border-radius:var(--r-md);
  border:1.5px solid var(--border);font-size:1rem;color:var(--ink-1);outline:none;
  touch-action:manipulation;-webkit-user-select:text;user-select:text;
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);}
.field input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}
.field-hint{font-size:.75rem;color:var(--ink-3);margin-top:4px;}
.field-group{display:flex;gap:12px;}
.field-group .field{flex:1;}
.btn-submit{width:100%;height:48px;border-radius:var(--r-md);
  font-size:.9375rem;font-weight:600;color:#fff;border:none;
  transition:all var(--dur) var(--ease);}
.btn-submit:active{transform:scale(.98);}
.btn-submit.green{background:var(--accent);}
.btn-submit.green:hover{background:var(--accent-h);}
.btn-submit.blue{background:var(--blue);}
.btn-submit.blue:hover{background:#2D5FE0;}
.btn-submit.red{background:var(--red);}
.btn-submit.red:hover{background:#C73E42;}

/* Item card in modal */
.item-card{padding:12px 16px;background:var(--bg-subtle);border-radius:var(--r-md);margin-bottom:16px;}
.item-card-name{font-weight:500;font-size:.875rem;color:var(--ink-1);}
.item-card-row{font-size:.75rem;color:var(--ink-3);margin-top:2px;}

/* Delete modal */
.del-body{font-size:.875rem;color:var(--ink-2);margin-bottom:20px;line-height:1.6;}
.del-actions{display:flex;gap:12px;}
.del-actions .btn-submit{flex:1;height:44px;font-size:.875rem;}
.btn-cancel{flex:1;height:44px;border-radius:var(--r-md);background:var(--bg-subtle);
  color:var(--ink-2);font-size:.875rem;font-weight:600;border:none;
  transition:background var(--dur) var(--ease);}
.btn-cancel:hover{background:var(--border-s);}

/* Toast */
.toast{position:fixed;bottom:96px;left:50%;transform:translateX(-50%);z-index:40;
  background:var(--ink-1);color:#fff;padding:12px 20px;border-radius:var(--r-md);
  font-size:.875rem;box-shadow:var(--sh-lg);display:flex;align-items:center;gap:12px;
  animation:toastIn var(--dur) var(--ease),toastOut var(--dur) var(--ease) 2.7s forwards;}
.toast.error{background:var(--red);}
.toast button{color:#7AB8FF;font-weight:600;font-size:.8125rem;border:none;background:none;}
@keyframes toastIn{from{opacity:0;transform:translate(-50%,20px);}to{opacity:1;transform:translate(-50%,0);}}
@keyframes toastOut{to{opacity:0;transform:translate(-50%,20px);}}

/* History date bar */
.date-bar{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px;}
.date-field{flex:1;min-width:130px;}
.date-field label{display:block;font-size:.75rem;color:var(--ink-3);margin-bottom:4px;font-weight:500;}
.date-field input{width:100%;height:40px;padding:0 10px;border-radius:var(--r-sm);
  border:1.5px solid var(--border);font-size:.875rem;color:var(--ink-1);outline:none;
  touch-action:manipulation;-webkit-user-select:text;user-select:text;
  transition:border-color var(--dur) var(--ease);}
.date-field input:focus{border-color:var(--accent);}
.date-actions{display:flex;gap:8px;}
.btn-today,.btn-apply{height:40px;padding:0 16px;border-radius:var(--r-sm);
  font-size:.8125rem;font-weight:600;transition:all var(--dur) var(--ease);border:none;}
.btn-today{background:var(--bg-subtle);color:var(--ink-2);}
.btn-today:hover{background:var(--border-s);}
.btn-apply{background:var(--accent);color:#fff;}
.btn-apply:hover{background:var(--accent-h);}
.date-error{font-size:.8125rem;color:var(--red);margin-bottom:12px;font-weight:500;}
.date-summary{font-size:.8125rem;color:var(--ink-3);margin-bottom:12px;font-weight:500;}

/* Timeline */
.timeline{position:relative;padding-left:24px;}
.timeline-line{position:absolute;left:7px;top:4px;bottom:4px;width:2px;background:var(--border);}
.timeline-item{position:relative;margin-bottom:12px;}
.timeline-dot{position:absolute;left:-20px;top:14px;width:12px;height:12px;
  border-radius:var(--r-full);border:2.5px solid var(--bg-surface);}
.timeline-dot.in{background:var(--accent);}
.timeline-dot.out{background:var(--blue);}
.timeline-card{background:var(--bg-surface);border:1px solid var(--border);
  border-radius:var(--r-lg);padding:14px 16px;}
.timeline-head{display:flex;align-items:center;justify-content:space-between;}
.timeline-left{display:flex;align-items:center;gap:8px;flex:1;min-width:0;}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:var(--r-sm);
  font-size:.6875rem;font-weight:600;}
.badge.in{background:var(--accent-bg);color:var(--accent);}
.badge.out{background:var(--blue-bg);color:var(--blue);}
.timeline-name{font-weight:500;font-size:.875rem;color:var(--ink-1);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.timeline-qty{font-size:.9375rem;font-weight:600;flex-shrink:0;margin-left:8px;}
.timeline-qty.in{color:var(--accent);}
.timeline-qty.out{color:var(--blue);}
.timeline-meta{font-size:.75rem;color:var(--ink-3);margin-top:6px;display:flex;flex-wrap:wrap;gap:8px;}
.timeline-meta span{white-space:nowrap;}
.timeline-time{font-size:.75rem;color:var(--ink-4);margin-top:4px;}

/* Login */
.login-body{background:var(--bg-canvas);min-height:100dvh;display:flex;
  align-items:center;justify-content:center;padding:40px 20px;}
.login-wrap{width:100%;max-width:380px;}
.login-brand{text-align:center;margin-bottom:28px;}
.login-icon{width:56px;height:56px;border-radius:var(--r-lg);background:var(--accent);
  box-shadow:0 8px 24px rgba(0,168,120,.2);margin:0 auto 16px;
  display:flex;align-items:center;justify-content:center;}
.login-icon svg{width:28px;height:28px;color:#fff;}
.login-title{font-size:1.5rem;font-weight:700;color:var(--ink-1);}
.login-sub{font-size:.8125rem;color:var(--ink-3);margin-top:8px;}
.login-card{background:var(--bg-surface);border:1px solid var(--border);
  border-radius:var(--r-xl);box-shadow:var(--sh-lg);padding:28px 24px;}
.login-error{border-radius:var(--r-md);background:var(--red-bg);padding:12px 16px;
  font-size:.8125rem;color:var(--red);margin-bottom:16px;}
.pw-wrap{position:relative;}
.pw-toggle{position:absolute;right:0;top:0;width:48px;height:48px;
  display:flex;align-items:center;justify-content:center;color:var(--ink-3);
  transition:color var(--dur) var(--ease);border:none;background:none;}
.pw-toggle:hover{color:var(--ink-1);}
.pw-toggle svg{width:20px;height:20px;}
"""

# ════════════════════════════════════════════════════════════
#  HTML Templates
# ════════════════════════════════════════════════════════════

_FAVICON = (
    '<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22'
    ' viewBox=%220 0 64 64%22><rect width=%2264%22 height=%2264%22 rx=%2216%22'
    ' fill=%22%2300A878%22/><path d=%22M18 23l14-7 14 7v18l-14 7-14-7V23zm14-7v32'
    ' m-14-25 14 7 14-7%22 fill=%22none%22 stroke=%22white%22 stroke-width=%224%22'
    ' stroke-linejoin=%22round%22/></svg>">'
)

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>库存管理</title>
""" + _FAVICON + """
<style>""" + _CSS + """</style>
</head>
<body>

<header class="hdr">
  <div class="hdr-in">
    <div class="hdr-row">
      <h1 class="hdr-title">库存</h1>
      <div class="menu-wrap">
        <button class="menu-btn" onclick="toggleMenu()" aria-label="菜单" aria-expanded="false">
          <svg fill="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
        <div class="menu-panel" id="menu">
          <a href="/history">历史记录</a>
          <a href="/export">导出CSV</a>
          <div class="divider"></div>
          <form action="/logout" method="POST"><button type="submit" class="danger">退出登录</button></form>
        </div>
      </div>
    </div>
    <div class="search-wrap">
      <svg class="search-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m21 21-4.35-4.35m1.35-5.65a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"/></svg>
      <label for="inventory-search" class="sr-only">搜索品牌或型号</label>
      <input id="inventory-search" type="search" placeholder="搜索品牌或型号" autocomplete="off" class="search-input">
    </div>
  </div>
</header>

<main class="main">
{% if not has_items %}
  <div class="empty">
    <div class="empty-circle">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-14L4 7m8 4v10M4 7v10l8 4"/></svg>
    </div>
    <p class="empty-title">还没有库存数据</p>
    <p class="empty-hint">点击下方「入库」添加</p>
  </div>
{% else %}
  <div style="display:flex;gap:12px;">
    <div style="flex:1;">
    {% for initial in sorted_initials %}
      <div id="section-{{ initial }}" class="initial-section" data-initial="{{ initial }}" style="padding-top:12px;">
        <div style="font-size:.75rem;font-weight:600;color:var(--ink-3);padding:0 4px 8px;">{{ initial }}</div>
        {% for group in groups.get(initial, []) %}
        <section class="brand-group" data-brand-group data-brand="{{ group['brand']|lower }}">
          <button type="button" class="brand-toggle" aria-expanded="false">
            <span class="brand-info">
              <span class="brand-name">{{ group['brand'] }}</span>
              <span class="brand-meta">{{ group['items']|length }} 个型号 · 共 {{ group['total_quantity'] }} 件</span>
            </span>
            <svg class="chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 18 6-6-6-6"/></svg>
          </button>
          <div class="brand-models">
          {% for item in group['items'] %}
            <div class="model-row {{ 'empty-stock' if item.quantity == 0 else '' }}" data-search="{{ (item.brand ~ ' ' ~ item.model)|lower }}">
              <div class="model-info">
                <div class="model-name">{{ item.model }}</div>
                <div class="model-qty">数量：{{ item.quantity }}</div>
                <div class="model-price">进价：¥{{ '%d.%02d'|format(item.unit_cost_cents // 100, item.unit_cost_cents % 100) }}</div>
              </div>
              <div class="model-actions">
                <button class="icon-btn out {{ 'off' if item.quantity == 0 else '' }}" aria-label="出库 {{ item.brand }} {{ item.model }}" onclick='openOutModal({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }},{{ item.quantity }},{{ ('%d.%02d'|format(item.unit_cost_cents // 100, item.unit_cost_cents % 100))|tojson }})'><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14"/></svg></button>
                <button class="icon-btn in" aria-label="入库 {{ item.brand }} {{ item.model }}" onclick='openInModal({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }},{{ ('%d.%02d'|format(item.unit_cost_cents // 100, item.unit_cost_cents % 100))|tojson }})'><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 5v14M5 12h14"/></svg></button>
                <button class="icon-btn del" aria-label="删除 {{ item.brand }} {{ item.model }}" onclick='deleteItem({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }})'><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg></button>
              </div>
            </div>
          {% endfor %}
          </div>
        </section>
        {% endfor %}
      </div>
    {% endfor %}
    </div>
    <div class="alpha-bar">
    {% for initial in alphabets %}
      {% if groups.get(initial) %}
      <a data-alphabet href="#section-{{ initial }}" aria-label="跳转到 {{ initial }}" class="alpha-link">{{ initial }}</a>
      {% else %}
      <span data-alphabet aria-disabled="true" class="alpha-link off">{{ initial }}</span>
      {% endif %}
    {% endfor %}
    </div>
  </div>
{% endif %}
</main>

<div class="capsule">
  <button class="capsule-half" onclick="openInModal()">
    <span class="sign">＋</span><span>入库</span>
  </button>
  <button class="capsule-half {{ 'off' if not has_items else '' }}" onclick="openOutPicker()">
    <span class="sign">－</span><span>出库</span>
  </button>
</div>

<div id="in-modal" class="modal-bg" role="dialog" aria-modal="true" aria-labelledby="in-modal-title" inert>
  <div class="modal-card">
    <div class="modal-head">
      <h2 id="in-modal-title" class="modal-title">入库</h2>
      <button class="modal-close" onclick="closeInModal()" aria-label="关闭"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <form action="/stock-in" method="POST">
      <div class="field"><label for="in-brand">品牌</label><input name="brand" id="in-brand" type="text" required placeholder="例：倍耐力"></div>
      <div class="field"><label for="in-model">型号</label><input name="model" id="in-model" type="text" required placeholder="例：26540R22"></div>
      <div class="field-group">
        <div class="field"><label for="in-quantity">数量</label><input name="quantity" id="in-quantity" type="number" min="1" value="1" required></div>
        <div class="field"><label for="in-price">进价（元）</label><input name="price" id="in-price" type="number" min="0" step="0.01" value="0.00" inputmode="decimal" required></div>
      </div>
      <p class="field-hint">相同品牌+型号会累加数量，并更新为本次进价</p>
      <button type="submit" class="btn-submit green">确认入库</button>
    </form>
  </div>
</div>

<div id="out-picker" class="modal-bg" role="dialog" aria-modal="true" aria-labelledby="out-picker-title" inert>
  <div class="modal-card" style="max-height:80vh;display:flex;flex-direction:column;">
    <div class="modal-head">
      <h2 id="out-picker-title" class="modal-title">选择出库商品</h2>
      <button class="modal-close" onclick="closeOutPicker()" aria-label="关闭"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <div style="overflow-y:auto;flex:1;">
    {% for initial in sorted_initials %}
      {% for group in groups.get(initial, []) %}
      {% set picker_items = group['items']|selectattr('quantity','>',0)|list %}
      {% if picker_items %}
      <section class="brand-group" style="margin-bottom:8px;">
        <button type="button" class="brand-toggle picker-brand-toggle" aria-expanded="false">
          <span class="brand-info">
            <span class="brand-name">{{ group['brand'] }}</span>
            <span class="brand-meta">{{ picker_items|length }} 个型号 · 共 {{ picker_items|sum(attribute='quantity') }} 件</span>
          </span>
          <svg class="chevron picker-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 18 6-6-6-6"/></svg>
        </button>
        <div class="brand-models picker-models">
        {% for item in picker_items %}
          <button onclick='openOutModal({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }},{{ item.quantity }},{{ ('%d.%02d'|format(item.unit_cost_cents // 100, item.unit_cost_cents % 100))|tojson }})' style="width:100%;text-align:left;padding:12px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border);transition:background var(--dur) var(--ease);">
            <div style="flex:1;min-width:0;">
              <div class="model-name">{{ item.model }}</div>
              <div class="model-qty">数量：{{ item.quantity }}</div>
              <div class="model-price">进价：¥{{ '%d.%02d'|format(item.unit_cost_cents // 100, item.unit_cost_cents % 100) }}</div>
            </div>
            <svg style="width:18px;height:18px;color:var(--blue);flex-shrink:0;" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14"/></svg>
          </button>
        {% endfor %}
        </div>
      </section>
      {% endif %}
      {% endfor %}
    {% endfor %}
    </div>
  </div>
</div>

<div id="out-modal" class="modal-bg" role="dialog" aria-modal="true" aria-labelledby="out-modal-title" inert>
  <div class="modal-card">
    <div class="modal-head">
      <h2 id="out-modal-title" class="modal-title">出库</h2>
      <button class="modal-close" onclick="closeOutModal()" aria-label="关闭"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <div class="item-card">
      <div class="item-card-name" id="out-item-name">—</div>
      <div class="item-card-row">数量：<span id="out-item-qty">0</span></div>
      <div class="item-card-row">进价：¥<span id="out-item-price">0.00</span></div>
    </div>
    <form id="out-form" action="/stock-out" method="POST">
      <input type="hidden" name="item_id" id="out-item-id">
      <div class="field"><label for="out-quantity">出库数量</label><input name="quantity" id="out-quantity" type="number" min="1" value="1" required></div>
      <p class="field-hint">超出库存的数量会自动截断</p>
      <button type="submit" class="btn-submit blue">确认出库</button>
    </form>
  </div>
</div>

<div id="delete-modal" class="modal-bg" role="dialog" aria-modal="true" aria-labelledby="delete-modal-title" inert>
  <div class="modal-card" style="max-width:340px;">
    <div class="modal-head">
      <h2 id="delete-modal-title" class="modal-title">删除商品</h2>
      <button class="modal-close" onclick="closeDeleteModal()" aria-label="关闭"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <p class="del-body" id="delete-desc">确定删除该商品吗？历史记录将保留。</p>
    <form id="delete-form" action="" method="POST" class="del-actions">
      <button type="button" class="btn-cancel" onclick="closeDeleteModal()">取消</button>
      <button type="submit" class="btn-submit red">删除</button>
    </form>
  </div>
</div>

{% if qp.get('added') %}
<div class="toast">入库成功</div>
{% elif qp.get('out') and qp.get('qty') %}
<div class="toast"><span>已出库 {{ qp.get('qty') }} 件</span><form action="/stock-out/undo" method="POST"><input type="hidden" name="item_id" value="{{ qp.get('out') }}"><input type="hidden" name="quantity" value="{{ qp.get('qty') }}"><button type="submit">撤销</button></form></div>
{% elif qp.get('undone') %}
<div class="toast">已撤销出库</div>
{% elif qp.get('deleted') %}
<div class="toast">已删除</div>
{% elif qp.get('error') %}
<div class="toast error">输入有误，请检查</div>
{% endif %}

<script>
let lastFocus=null;
function syncModals(){
  document.querySelectorAll('.modal-bg').forEach(modal=>{
    const open=modal.classList.contains('open');
    modal.inert=!open;
    modal.setAttribute('aria-hidden',String(!open));
  });
}
function openModal(id){
  lastFocus=document.activeElement;
  document.querySelectorAll('.modal-bg').forEach(modal=>modal.classList.remove('open'));
  const modal=document.getElementById(id);
  modal.classList.add('open');
  syncModals();
  setTimeout(()=>{
    const field=modal.querySelector('input:not([type=hidden])');
    if(field)field.focus({preventScroll:true});
  },50);
}
function closeModal(id){
  const modal=document.getElementById(id);
  modal.classList.remove('open');
  syncModals();
  if(lastFocus)lastFocus.focus();
}
syncModals();
document.querySelectorAll('.modal-card').forEach(card=>{
  card.addEventListener('click',event=>event.stopPropagation());
});
function toggleMenu(){const m=document.getElementById('menu');const b=document.querySelector('.menu-btn');m.classList.toggle('open');b.setAttribute('aria-expanded',String(m.classList.contains('open')));}
document.addEventListener('click',e=>{const w=document.querySelector('.menu-wrap');if(w&&!w.contains(e.target)){const m=document.getElementById('menu');m.classList.remove('open');document.querySelector('.menu-btn').setAttribute('aria-expanded','false');}});
function openInModal(id,brand,model,price){document.getElementById('in-brand').value=brand||'';document.getElementById('in-model').value=model||'';document.getElementById('in-price').value=price||'0.00';document.getElementById('in-quantity').value=1;openModal('in-modal');}
function closeInModal(){closeModal('in-modal');}
function openOutPicker(){openModal('out-picker');}
function closeOutPicker(){closeModal('out-picker');}
function openOutModal(id,brand,model,qty,price){document.getElementById('out-item-id').value=id;document.getElementById('out-item-name').textContent=brand+' · '+model;document.getElementById('out-item-qty').textContent=qty;document.getElementById('out-item-price').textContent=price;const q=document.getElementById('out-quantity');q.value=1;q.max=qty;closeOutPicker();openModal('out-modal');}
function closeOutModal(){closeModal('out-modal');}
function deleteItem(id,brand,model){document.getElementById('delete-desc').textContent='确定删除「'+brand+' · '+model+'」吗？历史记录将保留。';document.getElementById('delete-form').action='/delete/'+id;openModal('delete-modal');}
function closeDeleteModal(){closeModal('delete-modal');}
function toggleBrandGroup(button){
  const models=button.nextElementSibling;
  if(!models)return;
  const expanded=button.getAttribute('aria-expanded')==='true';
  button.setAttribute('aria-expanded',String(!expanded));
  models.classList.toggle('open',!expanded);
  const chevron=button.querySelector('.chevron');
  if(chevron)chevron.classList.toggle('open',!expanded);
}
document.querySelectorAll('.brand-toggle:not(.picker-brand-toggle)').forEach(button=>{
  button.addEventListener('click',()=>toggleBrandGroup(button));
});
document.querySelectorAll('.picker-brand-toggle').forEach(button=>{
  button.addEventListener('click',()=>toggleBrandGroup(button));
});
document.getElementById('inventory-search').addEventListener('input',event=>{
  const query=event.target.value.trim().toLocaleLowerCase('zh-CN');
  document.querySelectorAll('[data-brand-group]').forEach(group=>{
    let visible=0;
    group.querySelectorAll('.model-row').forEach(row=>{
      const matched=!query||row.dataset.search.includes(query);
      row.style.display=matched?'':'none';
      if(matched)visible+=1;
    });
    const groupMatched=!query||group.dataset.brand.includes(query)||visible>0;
    group.style.display=groupMatched?'':'none';
    if(query&&groupMatched){
      group.querySelector('.brand-models').classList.add('open');
      group.querySelector('.brand-toggle').setAttribute('aria-expanded','true');
      group.querySelector('.chevron').classList.add('open');
    }
  });
  document.querySelectorAll('.initial-section').forEach(section=>{
    const hasVisible=[...section.querySelectorAll('[data-brand-group]')].some(g=>g.style.display!=='none');
    section.style.display=query&&!hasVisible?'none':'';
  });
});
document.querySelectorAll('.modal-bg').forEach(el=>el.addEventListener('click',()=>{
  el.classList.remove('open');
  syncModals();
  if(lastFocus)lastFocus.focus();
}));
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){
    document.querySelectorAll('.modal-bg.open').forEach(modal=>modal.classList.remove('open'));
    syncModals();
    if(lastFocus)lastFocus.focus();
  }
});
</script>
</body>
</html>"""


HISTORY_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>历史记录 - 库存管理</title>
""" + _FAVICON + """
<style>""" + _CSS + """</style>
</head>
<body>

<header class="hdr">
  <div class="hdr-in">
    <div class="hdr-row">
      <a href="/" class="hdr-back" aria-label="返回"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg></a>
      <h1 class="hdr-title">历史记录</h1>
    </div>
  </div>
</header>

<main class="main">
  <form action="/history" method="GET" class="date-bar">
    <div class="date-field">
      <label for="start-date">开始日期</label>
      <input type="date" id="start-date" name="start" value="{{ start_str }}" max="{{ today_str }}">
    </div>
    <div class="date-field">
      <label for="end-date">结束日期</label>
      <input type="date" id="end-date" name="end" value="{{ end_str }}" max="{{ today_str }}">
    </div>
    <div class="date-actions">
      <button type="button" class="btn-today" onclick="document.getElementById('start-date').value='{{ today_str }}';document.getElementById('end-date').value='{{ today_str }}';">今天</button>
      <button type="submit" class="btn-apply">查询</button>
    </div>
  </form>

  {% if date_errors %}
  <p class="date-error">{{ date_errors }}</p>
  {% endif %}

  {% if not date_errors %}
  <p class="date-summary">{{ start_str }} 至 {{ end_str }} · 共 {{ records|length }} 条记录</p>
  {% endif %}

  {% if not date_errors and not records %}
  <div class="empty">
    <div class="empty-circle">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l0 4M8 8H16M8 16H16M4 4h16v16H4z"/></svg>
    </div>
    <p class="empty-title">该日期范围内没有记录</p>
  </div>
  {% elif not date_errors %}
  <div class="timeline">
    <div class="timeline-line"></div>
    {% for r in records %}
    <div class="timeline-item">
      <div class="timeline-dot {{ r.type }}"></div>
      <div class="timeline-card">
        <div class="timeline-head">
          <div class="timeline-left">
            <span class="badge {{ r.type }}">{{ '入库' if r.type == 'in' else '出库' }}</span>
            <span class="timeline-name">{{ r.brand_snapshot }} · {{ r.model_snapshot }}</span>
          </div>
          <span class="timeline-qty {{ r.type }}">{{ '+' if r.type == 'in' else '−' }}{{ r.quantity }}</span>
        </div>
        <div class="timeline-meta">
          <span>进价：¥{{ '%d.%02d'|format(r.unit_cost_cents // 100, r.unit_cost_cents % 100) }}</span>
          <span>金额：¥{{ '%d.%02d'|format((r.unit_cost_cents * r.quantity) // 100, (r.unit_cost_cents * r.quantity) % 100) }}</span>
        </div>
        <div class="timeline-time">{{ r.occurred_at }}</div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
</main>
</body>
</html>"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录 - 库存管理</title>
""" + _FAVICON + """
<style>""" + _CSS + """</style>
</head>
<body class="login-body">
  <main class="login-wrap">
    <div class="login-brand">
      <div class="login-icon">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="m4 7 8-4 8 4-8 4-8-4Zm0 0v10l8 4 8-4V7M12 11v10"/></svg>
      </div>
      <h1 class="login-title">库存管理</h1>
      <p class="login-sub">登录后管理入库、出库与进价</p>
    </div>
    <div class="login-card">
      <form action="/login" method="POST">
        <input type="hidden" name="next" value="{{ next_path }}">
        <div class="field">
          <label for="username">用户名</label>
          <input id="username" name="username" type="text" autocomplete="username" required autofocus>
        </div>
        <div class="field">
          <label for="password">密码</label>
          <div class="pw-wrap">
            <input id="password" name="password" type="password" autocomplete="current-password" required style="padding-right:48px;">
            <button type="button" class="pw-toggle" aria-label="显示密码" aria-pressed="false">
              <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/></svg>
            </button>
          </div>
        </div>
        {% if error %}<p role="alert" class="login-error">用户名或密码错误，请重试。</p>{% endif %}
        <button type="submit" class="btn-submit green">登录</button>
      </form>
    </div>
  </main>
<script>
const password=document.getElementById('password');
document.querySelector('.pw-toggle').addEventListener('click',event=>{
  const button=event.currentTarget;
  const visible=password.type==='text';
  password.type=visible?'password':'text';
  button.setAttribute('aria-pressed',String(!visible));
  button.setAttribute('aria-label',visible?'显示密码':'隐藏密码');
});
</script>
</body>
</html>"""


# ════════════════════════════════════════════════════════════
#  FastAPI App
# ════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="库存管理")
app.router.lifespan_context = lifespan


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path == "/login":
        return await call_next(request)
    if not request.session.get("authenticated"):
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(
            url=f"/login?next={quote(next_path, safe='')}", status_code=303
        )
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=False,
)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=303)
    html = TEMPLATES.from_string(LOGIN_HTML).render(
        error=False, next_path=safe_next_path(request.query_params.get("next"))
    )
    return HTMLResponse(html)


def _credentials_match(given: str, expected: str) -> bool:
    """Timing-safe compare; encode first so non-ASCII input won't raise."""
    try:
        return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, ValueError):
        return False


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    next_path = safe_next_path(next)
    valid = _credentials_match(username, AUTH_USER) and _credentials_match(
        password, AUTH_PASS
    )
    if not valid:
        html = TEMPLATES.from_string(LOGIN_HTML).render(
            error=True, next_path=next_path
        )
        return HTMLResponse(html, status_code=401)
    request.session.clear()
    request.session["authenticated"] = True
    return RedirectResponse(url=next_path, status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    items = get_all_items()
    grouped_items: dict[str, dict[str, list[dict]]] = {}
    for item in items:
        initial = item["pinyin_initial"]
        brand = item["brand"]
        grouped_items.setdefault(initial, {}).setdefault(brand, []).append(item)
    groups = {
        initial: [
            {
                "brand": brand,
                "items": sorted(brand_items, key=lambda item: model_sort_key(item["model"])),
                "total_quantity": sum(item["quantity"] for item in brand_items),
            }
            for brand, brand_items in brands.items()
        ]
        for initial, brands in grouped_items.items()
    }
    sorted_initials = sorted(groups.keys(), key=lambda initial: (initial == "#", initial))
    qp = dict(request.query_params)
    html = TEMPLATES.from_string(INDEX_HTML).render(
        groups=groups, alphabets=ALPHABETS, sorted_initials=sorted_initials,
        has_items=len(items) > 0, qp=qp,
    )
    return HTMLResponse(html)


@app.post("/stock-in")
def stock_in(
    brand: str = Form(...),
    model: str = Form(...),
    quantity: int = Form(...),
    price: str = Form(...),
):
    brand, model = brand.strip(), model.strip()
    price_cents = parse_price_cents(price)
    if not brand or not model or quantity <= 0 or price_cents is None:
        return RedirectResponse(url="/?error=invalid", status_code=303)
    add_or_restock(
        brand, model, quantity, pinyin_initial(brand + model), price_cents
    )
    return RedirectResponse(url="/?added=1", status_code=303)


@app.post("/stock-out")
def stock_out(item_id: int = Form(...), quantity: int = Form(...)):
    if quantity <= 0:
        return RedirectResponse(url="/?error=invalid", status_code=303)
    try:
        result = do_stock_out(item_id, quantity)
    except ValueError:
        return RedirectResponse(url="/?error=missing", status_code=303)
    return RedirectResponse(
        url=f"/?out={item_id}&qty={result['actual_out']}", status_code=303
    )


@app.post("/stock-out/undo")
def stock_out_undo(item_id: int = Form(...), quantity: int = Form(...)):
    if quantity <= 0:
        return RedirectResponse(url="/", status_code=303)
    item = get_item(item_id)
    if not item:
        return RedirectResponse(url="/", status_code=303)
    add_or_restock(
        item["brand"], item["model"], quantity, item["pinyin_initial"], None
    )
    return RedirectResponse(url="/?undone=1", status_code=303)


@app.post("/delete/{item_id}")
def delete(item_id: int):
    delete_item(item_id)
    return RedirectResponse(url="/?deleted=1", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    today = today_sha()
    today_str = today.isoformat()
    raw_start = request.query_params.get("start")
    raw_end = request.query_params.get("end")

    if not raw_start and not raw_end:
        start = end = today
    else:
        start = parse_date(raw_start)
        end = parse_date(raw_end)

    date_errors = None
    if start is None or end is None:
        date_errors = "日期格式无效，请使用日历选择。"
    elif start > end:
        date_errors = "开始日期不能晚于结束日期。"

    if date_errors:
        start = end = today
        records = []
    else:
        records = get_stock_history(start, end)

    html = TEMPLATES.from_string(HISTORY_HTML).render(
        records=records,
        start_str=start.isoformat(),
        end_str=end.isoformat(),
        today_str=today_str,
        date_errors=date_errors,
    )
    return HTMLResponse(html)


@app.get("/export")
def export_csv():
    content = build_csv()
    filename = f"inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10001)