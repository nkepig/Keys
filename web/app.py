"""
库存管理 — 单文件 FastAPI + SQLite Web 应用。
运行：python3.13 app.py  →  http://localhost:8000
依赖：fastapi uvicorn jinja2 pypinyin python-multipart
"""

import sqlite3
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional
from base64 import b64decode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from jinja2 import Environment
from pypinyin import pinyin, Style

# ════════════════════════════════════════════════════════════
#  Database
# ════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).parent / "inventory.db"
ALPHABETS: tuple[str, ...] = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ("#",)
TEMPLATES = Environment(autoescape=True)

AUTH_USER = "root"
AUTH_PASS = "asd12345"


def _check_auth(auth_header: str | None) -> bool:
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = b64decode(auth_header[6:]).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    user, _, password = decoded.partition(":")
    return secrets.compare_digest(user, AUTH_USER) and secrets.compare_digest(password, AUTH_PASS)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            brand          TEXT NOT NULL,
            model          TEXT NOT NULL,
            quantity       INTEGER NOT NULL DEFAULT 0,
            pinyin_initial TEXT NOT NULL DEFAULT '#',
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(brand, model)
        );
        CREATE TABLE IF NOT EXISTS stock_records (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id    INTEGER NOT NULL,
            type       TEXT NOT NULL CHECK(type IN ('in','out')),
            quantity   INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES inventory_items(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sr_item ON stock_records(item_id);
        CREATE INDEX IF NOT EXISTS idx_sr_time  ON stock_records(created_at);
    """)
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


def add_or_restock(brand: str, model: str, quantity: int, pinyin_initial: str) -> dict:
    c = _conn()
    ex = c.execute(
        "SELECT * FROM inventory_items WHERE brand=? AND model=?", (brand, model)
    ).fetchone()
    if ex:
        new_qty = ex["quantity"] + quantity
        c.execute(
            "UPDATE inventory_items SET quantity=?, updated_at=datetime('now') WHERE id=?",
            (new_qty, ex["id"]),
        )
        c.execute(
            "INSERT INTO stock_records (item_id, type, quantity) VALUES (?, 'in', ?)",
            (ex["id"], quantity),
        )
        c.commit()
        item = dict(c.execute(
            "SELECT * FROM inventory_items WHERE id=?", (ex["id"],)
        ).fetchone())
    else:
        cur = c.execute(
            "INSERT INTO inventory_items (brand, model, quantity, pinyin_initial) VALUES (?,?,?,?)",
            (brand, model, quantity, pinyin_initial),
        )
        iid = cur.lastrowid
        c.execute(
            "INSERT INTO stock_records (item_id, type, quantity) VALUES (?, 'in', ?)",
            (iid, quantity),
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
        "UPDATE inventory_items SET quantity=?, updated_at=datetime('now') WHERE id=?",
        (item["quantity"] - actual, item_id),
    )
    if actual > 0:
        c.execute(
            "INSERT INTO stock_records (item_id, type, quantity) VALUES (?, 'out', ?)",
            (item_id, actual),
        )
    c.commit()
    result = dict(c.execute(
        "SELECT * FROM inventory_items WHERE id=?", (item_id,)
    ).fetchone())
    c.close()
    return {"item": result, "actual_out": actual}


def delete_item(item_id: int) -> None:
    c = _conn()
    c.execute("DELETE FROM stock_records WHERE item_id=?", (item_id,))
    c.execute("DELETE FROM inventory_items WHERE id=?", (item_id,))
    c.commit()
    c.close()


def get_stock_history() -> list[dict]:
    c = _conn()
    rows = c.execute("""
        SELECT sr.id, sr.item_id, sr.type, sr.quantity, sr.created_at,
               ii.brand, ii.model
        FROM stock_records sr
        JOIN inventory_items ii ON sr.item_id = ii.id
        ORDER BY sr.created_at DESC
    """).fetchall()
    c.close()
    return [dict(r) for r in rows]


def build_csv() -> str:
    lines = ["\ufeff品牌,型号,数量"]
    for item in get_all_items():
        lines.append(f"{item['brand']},{item['model']},{item['quantity']}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  Pinyin
# ════════════════════════════════════════════════════════════

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
#  HTML Templates (inline)
# ════════════════════════════════════════════════════════════

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>库存管理</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22><rect width=%2264%22 height=%2264%22 rx=%2216%22 fill=%22%2334C759%22/><path d=%22M18 23l14-7 14 7v18l-14 7-14-7V23zm14-7v32m-14-25 14 7 14-7%22 fill=%22none%22 stroke=%22white%22 stroke-width=%224%22 stroke-linejoin=%22round%22/></svg>">
<script src="https://cdn.tailwindcss.com"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
body{font-family:'Inter',system-ui,-apple-system,sans-serif;}
.capsule-half{transition:transform .2s ease,opacity .2s ease;}
.capsule-half:active{transform:scale(.96);opacity:.7;}
.modal-backdrop{backdrop-filter:blur(4px);}
.toast{animation:slideUp .3s ease,fadeOut .3s ease 2.7s forwards;}
@keyframes slideUp{from{transform:translateY(100%);opacity:0;}to{transform:translateY(0);opacity:1;}}
@keyframes fadeOut{to{opacity:0;transform:translateY(20px);}}
.scroll-hidden::-webkit-scrollbar{display:none;}
</style>
</head>
<body class="bg-gray-50 min-h-screen">

<header class="sticky top-0 z-30 bg-white/80 backdrop-blur-md border-b border-gray-200">
  <div class="max-w-3xl mx-auto px-5 pt-4 pb-3">
    <div class="flex items-center">
      <h1 class="text-xl font-bold text-gray-900">库存</h1>
      <div class="flex-1"></div>
      <div class="relative" id="menu-wrapper">
      <button onclick="toggleMenu()" class="p-2 rounded-lg hover:bg-gray-100 transition">
        <svg class="w-6 h-6 text-gray-700" fill="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
      </button>
      <div id="menu" class="hidden absolute right-0 mt-2 w-36 bg-white rounded-xl shadow-lg border border-gray-100 overflow-hidden">
        <a href="/history" class="block px-4 py-3 text-sm text-gray-700 hover:bg-gray-50 transition">历史记录</a>
        <a href="/export" class="block px-4 py-3 text-sm text-gray-700 hover:bg-gray-50 transition">导出CSV</a>
      </div>
      </div>
    </div>
    <label for="inventory-search" class="sr-only">搜索品牌或型号</label>
    <div class="relative mt-3">
      <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m21 21-4.35-4.35m1.35-5.65a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"/></svg>
      <input id="inventory-search" type="search" placeholder="搜索品牌或型号" autocomplete="off" class="w-full h-11 pl-10 pr-4 rounded-xl bg-gray-100 border border-transparent text-base text-gray-900 placeholder:text-gray-400 outline-none focus:bg-white focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition">
    </div>
  </div>
</header>

<main class="max-w-3xl mx-auto px-5 pt-2 pb-32">
{% if not has_items %}
  <div class="flex flex-col items-center justify-center pt-32">
    <div class="w-20 h-20 rounded-full bg-gray-100 flex items-center justify-center mb-4">
      <svg class="w-10 h-10 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-14L4 7m8 4v10M4 7v10l8 4"/></svg>
    </div>
    <p class="text-gray-400 text-base">还没有库存数据</p>
    <p class="text-gray-300 text-sm mt-1">点击下方「入库」添加</p>
  </div>
{% else %}
  <div class="flex gap-3">
    <div class="flex-1 space-y-1">
    {% for initial in sorted_initials %}
      <div id="section-{{ initial }}" class="pt-3 initial-section" data-initial="{{ initial }}">
        <div class="text-xs font-semibold text-gray-400 px-1 pb-2">{{ initial }}</div>
        {% for group in groups.get(initial, []) %}
        <section class="brand-group mb-2 bg-white rounded-2xl border border-gray-100 overflow-hidden" data-brand-group data-brand="{{ group['brand']|lower }}">
          <button type="button" class="brand-toggle w-full min-h-14 px-4 py-3 flex items-center text-left hover:bg-gray-50 transition" aria-expanded="false">
            <span class="flex-1 min-w-0">
              <span class="block font-semibold text-gray-900">{{ group['brand'] }}</span>
              <span class="block text-xs text-gray-400 mt-0.5">{{ group['items']|length }} 个型号 · 共 {{ group['total_quantity'] }} 件</span>
            </span>
            <svg class="brand-chevron w-5 h-5 text-gray-400 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 18 6-6-6-6"/></svg>
          </button>
          <div class="brand-models hidden border-t border-gray-100">
          {% for item in group['items'] %}
            <div class="model-row px-4 py-3 flex items-center border-b last:border-b-0 border-gray-100 {{ 'opacity-40' if item.quantity == 0 else '' }}" data-search="{{ (item.brand ~ ' ' ~ item.model)|lower }}">
              <div class="flex-1 min-w-0">
                <div class="font-medium text-gray-900 text-sm">{{ item.model }}</div>
                <div class="text-xs text-gray-400 mt-0.5">数量：{{ item.quantity }}</div>
              </div>
              <div class="flex items-center gap-2 ml-3">
                <button aria-label="{{ item.brand }} {{ item.model }} 出库" onclick='openOutModal({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }},{{ item.quantity }})' class="w-11 h-11 rounded-full bg-blue-50 text-blue-500 flex items-center justify-center hover:bg-blue-100 transition {{ 'pointer-events-none opacity-30' if item.quantity == 0 else '' }}"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14"/></svg></button>
                <button aria-label="{{ item.brand }} {{ item.model }} 入库" onclick='openInModal({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }})' class="w-11 h-11 rounded-full bg-green-50 text-green-500 flex items-center justify-center hover:bg-green-100 transition"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 5v14M5 12h14"/></svg></button>
                <button aria-label="删除 {{ item.brand }} {{ item.model }}" onclick='deleteItem({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }})' class="w-11 h-11 rounded-full bg-gray-50 text-gray-400 flex items-center justify-center hover:bg-red-50 hover:text-red-500 transition"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg></button>
              </div>
            </div>
          {% endfor %}
          </div>
        </section>
        {% endfor %}
      </div>
    {% endfor %}
    </div>
    <div class="fixed right-1 top-1/2 -translate-y-1/2 flex flex-col items-center gap-0.5 text-[10px] text-gray-400 select-none scroll-hidden overflow-y-auto max-h-[70vh]">
    {% for initial in alphabets %}
      {% if groups.get(initial) %}
      <a data-alphabet href="#section-{{ initial }}" aria-label="跳转到 {{ initial }}" class="w-6 h-5 flex items-center justify-center rounded text-gray-700 font-semibold hover:bg-gray-200 transition">{{ initial }}</a>
      {% else %}
      <span data-alphabet aria-disabled="true" class="w-6 h-5 flex items-center justify-center text-gray-300">{{ initial }}</span>
      {% endif %}
    {% endfor %}
    </div>
  </div>
{% endif %}
</main>

<div class="fixed bottom-6 left-1/2 -translate-x-1/2 z-30">
  <div class="flex w-48 h-14 rounded-full bg-white border border-gray-200 shadow-lg overflow-hidden">
    <button onclick="openInModal()" class="capsule-half w-1/2 h-full flex items-center justify-center gap-1.5 bg-green-50/50 rounded-l-full">
      <span class="text-green-500 font-bold text-base">＋</span><span class="text-gray-900 font-medium text-sm">入库</span>
    </button>
    <button onclick="openOutPicker()" class="capsule-half w-1/2 h-full flex items-center justify-center gap-1.5 bg-blue-50/50 rounded-r-full {{ 'opacity-40 pointer-events-none' if not has_items else '' }}">
      <span class="text-blue-500 font-bold text-base">－</span><span class="text-gray-900 font-medium text-sm">出库</span>
    </button>
  </div>
</div>

<div id="in-modal" class="hidden fixed inset-0 z-50 modal-backdrop bg-black/30 flex items-end sm:items-center justify-center">
  <div class="bg-white w-full sm:max-w-md rounded-t-3xl sm:rounded-3xl p-6 shadow-2xl">
    <div class="flex items-center justify-between mb-5">
      <h2 class="text-lg font-semibold text-gray-900">入库</h2>
      <button onclick="closeInModal()" class="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center"><svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <form action="/stock-in" method="POST" class="space-y-4">
      <div><label class="text-sm text-gray-500 mb-1 block">品牌</label><input name="brand" id="in-brand" type="text" required placeholder="例：倍耐力" class="w-full px-4 py-3 rounded-xl border border-gray-200 focus:border-green-400 focus:ring-2 focus:ring-green-100 outline-none transition text-gray-900"></div>
      <div><label class="text-sm text-gray-500 mb-1 block">型号</label><input name="model" id="in-model" type="text" required placeholder="例：26540R22" class="w-full px-4 py-3 rounded-xl border border-gray-200 focus:border-green-400 focus:ring-2 focus:ring-green-100 outline-none transition text-gray-900"></div>
      <div><label class="text-sm text-gray-500 mb-1 block">数量</label><input name="quantity" type="number" min="1" value="1" required class="w-full px-4 py-3 rounded-xl border border-gray-200 focus:border-green-400 focus:ring-2 focus:ring-green-100 outline-none transition text-gray-900"></div>
      <p class="text-xs text-gray-400">相同品牌+型号会自动累加数量</p>
      <button type="submit" class="w-full py-3 bg-green-500 text-white font-medium rounded-xl hover:bg-green-600 transition active:scale-95">确认入库</button>
    </form>
  </div>
</div>

<div id="out-picker" class="hidden fixed inset-0 z-50 modal-backdrop bg-black/30 flex items-end sm:items-center justify-center">
  <div class="bg-white w-full sm:max-w-md rounded-t-3xl sm:rounded-3xl p-6 shadow-2xl max-h-[80vh] flex flex-col">
    <div class="flex items-center justify-between mb-5">
      <h2 class="text-lg font-semibold text-gray-900">选择出库商品</h2>
      <button onclick="closeOutPicker()" class="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center"><svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <div class="overflow-y-auto flex-1 space-y-1">
    {% for initial in sorted_initials %}
      {% for group in groups.get(initial, []) %}
      {% set picker_items = group['items']|selectattr('quantity','>',0)|list %}
      {% if picker_items %}
      <section class="picker-brand-group mb-2 bg-white rounded-2xl border border-gray-100 overflow-hidden">
        <button type="button" class="picker-brand-toggle w-full min-h-12 px-4 py-3 flex items-center text-left hover:bg-gray-50 transition" aria-expanded="false">
          <span class="flex-1 min-w-0">
            <span class="block font-semibold text-gray-900 text-sm">{{ group['brand'] }}</span>
            <span class="block text-xs text-gray-400 mt-0.5">{{ picker_items|length }} 个型号 · 共 {{ picker_items|sum(attribute='quantity') }} 件</span>
          </span>
          <svg class="picker-chevron w-5 h-5 text-gray-400 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 18 6-6-6-6"/></svg>
        </button>
        <div class="picker-models hidden border-t border-gray-100">
        {% for item in picker_items %}
          <button onclick='openOutModal({{ item.id }},{{ item.brand|tojson }},{{ item.model|tojson }},{{ item.quantity }})' class="w-full text-left px-4 py-3 flex items-center border-b last:border-b-0 border-gray-100 hover:bg-gray-50 transition">
            <div class="flex-1 min-w-0">
              <div class="font-medium text-gray-900 text-sm">{{ item.model }}</div>
              <div class="text-xs text-gray-400 mt-0.5">当前数量：{{ item.quantity }}</div>
            </div>
            <svg class="w-4 h-4 text-blue-400 ml-2 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14"/></svg>
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

<div id="out-modal" class="hidden fixed inset-0 z-[60] modal-backdrop bg-black/30 flex items-end sm:items-center justify-center">
  <div class="bg-white w-full sm:max-w-md rounded-t-3xl sm:rounded-3xl p-6 shadow-2xl">
    <div class="flex items-center justify-between mb-5">
      <h2 class="text-lg font-semibold text-gray-900">出库</h2>
      <button onclick="closeOutModal()" class="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center"><svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
    </div>
    <div class="mb-4 px-4 py-3 bg-gray-50 rounded-xl">
      <div class="font-medium text-gray-900 text-sm" id="out-item-name">—</div>
      <div class="text-xs text-gray-400 mt-0.5">当前数量：<span id="out-item-qty">0</span></div>
    </div>
    <form id="out-form" action="/stock-out" method="POST" class="space-y-4">
      <input type="hidden" name="item_id" id="out-item-id">
      <div><label class="text-sm text-gray-500 mb-1 block">出库数量</label><input name="quantity" type="number" min="1" value="1" required id="out-qty-input" class="w-full px-4 py-3 rounded-xl border border-gray-200 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 outline-none transition text-gray-900"></div>
      <p class="text-xs text-gray-400">超出库存的数量会自动截断</p>
      <button type="submit" class="w-full py-3 bg-blue-500 text-white font-medium rounded-xl hover:bg-blue-600 transition active:scale-95">确认出库</button>
    </form>
  </div>
</div>

<div id="delete-modal" class="hidden fixed inset-0 z-50 modal-backdrop bg-black/30 flex items-center justify-center">
  <div class="bg-white rounded-3xl p-6 shadow-2xl max-w-sm w-full mx-4">
    <p class="text-gray-900 font-medium mb-1">删除商品？</p>
    <p class="text-sm text-gray-500 mb-5" id="delete-desc">这将永久删除该商品及其历史记录。</p>
    <form id="delete-form" action="" method="POST" class="flex gap-3">
      <button type="button" onclick="closeDeleteModal()" class="flex-1 py-2.5 bg-gray-100 text-gray-700 font-medium rounded-xl hover:bg-gray-200 transition">取消</button>
      <button type="submit" class="flex-1 py-2.5 bg-red-500 text-white font-medium rounded-xl hover:bg-red-600 transition active:scale-95">删除</button>
    </form>
  </div>
</div>

{% if qp.get('added') %}
<div class="toast fixed bottom-24 left-1/2 -translate-x-1/2 z-40 bg-gray-900 text-white px-5 py-3 rounded-xl shadow-lg text-sm">入库成功</div>
{% elif qp.get('out') and qp.get('qty') %}
<div class="toast fixed bottom-24 left-1/2 -translate-x-1/2 z-40 bg-gray-900 text-white px-5 py-3 rounded-xl shadow-lg flex items-center gap-3 text-sm">
  <span>已出库 {{ qp.get('qty') }} 件</span>
  <form action="/stock-out/undo" method="POST"><input type="hidden" name="item_id" value="{{ qp.get('out') }}"><input type="hidden" name="quantity" value="{{ qp.get('qty') }}"><button type="submit" class="text-blue-300 font-medium">撤销</button></form>
</div>
{% elif qp.get('undone') %}
<div class="toast fixed bottom-24 left-1/2 -translate-x-1/2 z-40 bg-gray-900 text-white px-5 py-3 rounded-xl shadow-lg text-sm">已撤销出库</div>
{% elif qp.get('deleted') %}
<div class="toast fixed bottom-24 left-1/2 -translate-x-1/2 z-40 bg-gray-900 text-white px-5 py-3 rounded-xl shadow-lg text-sm">已删除</div>
{% elif qp.get('error') %}
<div class="toast fixed bottom-24 left-1/2 -translate-x-1/2 z-40 bg-red-500 text-white px-5 py-3 rounded-xl shadow-lg text-sm">输入有误，请检查</div>
{% endif %}

<script>
function toggleMenu(){document.getElementById('menu').classList.toggle('hidden');}
document.addEventListener('click',e=>{const w=document.getElementById('menu-wrapper');if(w&&!w.contains(e.target))document.getElementById('menu').classList.add('hidden');});
function openInModal(id,brand,model){document.getElementById('in-brand').value=brand||'';document.getElementById('in-model').value=model||'';document.getElementById('in-modal').classList.remove('hidden');}
function closeInModal(){document.getElementById('in-modal').classList.add('hidden');}
function openOutPicker(){document.getElementById('out-picker').classList.remove('hidden');}
function closeOutPicker(){document.getElementById('out-picker').classList.add('hidden');}
function openOutModal(id,brand,model,qty){document.getElementById('out-item-id').value=id;document.getElementById('out-item-name').textContent=brand+' · '+model;document.getElementById('out-item-qty').textContent=qty;const q=document.getElementById('out-qty-input');q.value=1;q.max=qty;closeOutPicker();document.getElementById('out-modal').classList.remove('hidden');}
function closeOutModal(){document.getElementById('out-modal').classList.add('hidden');}
function deleteItem(id,brand,model){document.getElementById('delete-desc').textContent='确定删除「'+brand+' · '+model+'」吗？这将永久删除该商品及其历史记录。';document.getElementById('delete-form').action='/delete/'+id;document.getElementById('delete-modal').classList.remove('hidden');}
function closeDeleteModal(){document.getElementById('delete-modal').classList.add('hidden');}
document.querySelectorAll('.brand-toggle').forEach(button=>button.addEventListener('click',()=>{
  const models=button.nextElementSibling;
  const expanded=button.getAttribute('aria-expanded')==='true';
  button.setAttribute('aria-expanded',String(!expanded));
  models.classList.toggle('hidden',expanded);
  button.querySelector('.brand-chevron').classList.toggle('rotate-90',!expanded);
}));
document.querySelectorAll('.picker-brand-toggle').forEach(button=>button.addEventListener('click',()=>{
  const models=button.nextElementSibling;
  const expanded=button.getAttribute('aria-expanded')==='true';
  button.setAttribute('aria-expanded',String(!expanded));
  models.classList.toggle('hidden',expanded);
  button.querySelector('.picker-chevron').classList.toggle('rotate-90',!expanded);
}));
document.getElementById('inventory-search').addEventListener('input',event=>{
  const query=event.target.value.trim().toLocaleLowerCase('zh-CN');
  document.querySelectorAll('[data-brand-group]').forEach(group=>{
    let visible=0;
    group.querySelectorAll('.model-row').forEach(row=>{
      const matched=!query||row.dataset.search.includes(query);
      row.classList.toggle('hidden',!matched);
      if(matched) visible+=1;
    });
    const groupMatched=!query||group.dataset.brand.includes(query)||visible>0;
    group.classList.toggle('hidden',!groupMatched);
    if(query&&groupMatched){
      group.querySelector('.brand-models').classList.remove('hidden');
      group.querySelector('.brand-toggle').setAttribute('aria-expanded','true');
      group.querySelector('.brand-chevron').classList.add('rotate-90');
    }
  });
  document.querySelectorAll('.initial-section').forEach(section=>{
    const hasVisible=[...section.querySelectorAll('[data-brand-group]')].some(group=>!group.classList.contains('hidden'));
    section.classList.toggle('hidden',query&& !hasVisible);
  });
});
document.querySelectorAll('.modal-backdrop').forEach(el=>el.addEventListener('click',e=>{if(e.target===el)el.classList.add('hidden');}));
</script>
</body>
</html>"""


HISTORY_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>历史记录 - 库存管理</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
body{font-family:'Inter',system-ui,-apple-system,sans-serif;}
</style>
</head>
<body class="bg-gray-50 min-h-screen">

<header class="sticky top-0 z-30 bg-white/80 backdrop-blur-md border-b border-gray-200">
  <div class="max-w-3xl mx-auto px-5 py-4 flex items-center">
    <a href="/" class="w-9 h-9 rounded-full hover:bg-gray-100 flex items-center justify-center transition mr-2"><svg class="w-5 h-5 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg></a>
    <h1 class="text-xl font-bold text-gray-900">历史记录</h1>
  </div>
</header>

<main class="max-w-3xl mx-auto px-5 pt-4 pb-10">
{% if not records %}
  <div class="flex flex-col items-center justify-center pt-32">
    <div class="w-20 h-20 rounded-full bg-gray-100 flex items-center justify-center mb-4">
      <svg class="w-10 h-10 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l0 4M8 8H16M8 16H16M4 4h16v16H4z"/></svg>
    </div>
    <p class="text-gray-400 text-base">还没有历史记录</p>
  </div>
{% else %}
  <div class="relative pl-6">
    <div class="absolute left-2 top-1 bottom-1 w-px bg-gray-200"></div>
    {% for r in records %}
    <div class="relative mb-4">
      <div class="absolute -left-[18px] top-3 w-3 h-3 rounded-full border-2 border-white {{ 'bg-green-400' if r.type == 'in' else 'bg-blue-400' }}"></div>
      <div class="bg-white rounded-2xl border border-gray-100 px-4 py-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium {{ 'bg-green-50 text-green-600' if r.type == 'in' else 'bg-blue-50 text-blue-600' }}">{{ '入库' if r.type == 'in' else '出库' }}</span>
            <span class="font-medium text-gray-900 text-sm">{{ r.brand }} · {{ r.model }}</span>
          </div>
          <span class="text-sm font-semibold {{ 'text-green-600' if r.type == 'in' else 'text-blue-600' }}">{{ '+' if r.type == 'in' else '−' }}{{ r.quantity }}</span>
        </div>
        <div class="text-xs text-gray-400 mt-1.5">{{ r.created_at }}</div>
      </div>
    </div>
    {% endfor %}
  </div>
{% endif %}
</main>
</body>
</html>"""


# ════════════════════════════════════════════════════════════
#  FastAPI App
# ════════════════════════════════════════════════════════════

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="库存管理")
app.router.lifespan_context = lifespan


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not _check_auth(request.headers.get("authorization")):
        return HTMLResponse(
            "<h1>401 未授权</h1><p>请输入正确用户名和密码。</p>",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="inventory"'},
        )
    return await call_next(request)


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
def stock_in(brand: str = Form(...), model: str = Form(...), quantity: int = Form(...)):
    brand, model = brand.strip(), model.strip()
    if not brand or not model or quantity <= 0:
        return RedirectResponse(url="/?error=invalid", status_code=303)
    add_or_restock(brand, model, quantity, pinyin_initial(brand + model))
    return RedirectResponse(url="/?added=1", status_code=303)


@app.post("/stock-out")
def stock_out(item_id: int = Form(...), quantity: int = Form(...)):
    if quantity <= 0:
        return RedirectResponse(url="/?error=invalid", status_code=303)
    result = do_stock_out(item_id, quantity)
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
    add_or_restock(item["brand"], item["model"], quantity, item["pinyin_initial"])
    return RedirectResponse(url="/?undone=1", status_code=303)


@app.post("/delete/{item_id}")
def delete(item_id: int):
    delete_item(item_id)
    return RedirectResponse(url="/?deleted=1", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history():
    records = get_stock_history()
    html = TEMPLATES.from_string(HISTORY_HTML).render(records=records)
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
