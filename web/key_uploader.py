from __future__ import annotations

import anyio
import hmac
import os
import secrets
from datetime import datetime
from typing import Any, Final

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.sessions import SessionMiddleware

API_KEY: Final = os.environ.get(
    "MSK_API_KEY", "msk_live_8499a96e4561ad3aa2b63ba3cc5a61505107db5a74a06a1b"
)
USERNAME: Final = os.environ.get("KEY_APP_USER", "root")
PASSWORD: Final = os.environ.get("KEY_APP_PASS", "asd12345")
SESSION_SECRET: Final = os.environ.get(
    "KEY_APP_SECRET", "k7m2x9q4z8r1v6t3y0w5a8b2c6d4e7f9"
)
BASE_URL: Final = "https://gys.oljjio.click/openapi/v1"
CATEGORIES: Final = {
    "anthropic": "Anthropic",
    "anthropic_small": "Anthropic (小额度)",
    "openai": "OpenAI",
    "aws": "AWS",
    "azure": "Azure",
    "ai_studio": "AI Studio",
}
BATCH_SIZE: Final = 2000
MAX_RETRIES: Final = 3
TIMEOUT: Final = 60.0


class UploadRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    category: str = "openai"
    keys_text: str = ""


def validate_key(key: str, category: str) -> bool:
    if category in ("anthropic", "anthropic_small"):
        return key.startswith("sk-ant-")
    if category == "openai":
        return key.startswith("sk-") and not key.startswith("sk-ant-")
    if category in ("aws", "azure"):
        parts = key.split("|")
        expected = 2 if category == "aws" else 3
        return len(parts) == expected and all(p.strip() for p in parts)
    if category == "ai_studio":
        return len(key) > 0
    return False


def clean_keys(text: str, category: str) -> tuple[list[str], int]:
    keys: list[str] = []
    seen: set[str] = set()
    invalid = 0
    for line in text.splitlines():
        key = line.strip()
        if not key:
            continue
        if not validate_key(key, category):
            invalid += 1
            continue
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys, invalid


async def post_batch(
    client: httpx.AsyncClient, tag: str, keys: list[str], category: str
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"category": category, "tag": tag, "keys": keys}
    last_error = "服务暂时不可用"

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.post(
                f"{BASE_URL}/channels",
                headers=headers,
                json=payload,
                timeout=TIMEOUT,
            )
            body = response.json()
        except (httpx.RequestError, ValueError) as exc:
            last_error = f"网络错误: {exc}"
            if attempt < MAX_RETRIES:
                await anyio.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(last_error) from exc

        code = body.get("code", -1)
        request_id = body.get("request_id", "-")
        if code == 0:
            return body
        if code == 40101:
            raise PermissionError(f"API Key 无效或停用（{request_id}）")
        if code == 40301:
            raise PermissionError(f"权限不足（{request_id}）")
        if code == 40001:
            raise ValueError(f"上传参数错误（{request_id}）")
        if response.status_code >= 500 or code == 50001:
            last_error = f"服务端错误（{request_id}）"
            if attempt < MAX_RETRIES:
                await anyio.sleep(2 ** (attempt + 1))
                continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)


async def upload_keys(text: str, category: str) -> dict[str, Any]:
    keys, invalid = clean_keys(text, category)
    if not keys:
        label = CATEGORIES.get(category, category)
        return {"ok": False, "error": f"请输入有效的 {label} Key"}

    tag = "batch-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    success = 0
    skipped = 0
    failed = 0
    async with httpx.AsyncClient() as client:
        for start in range(0, len(keys), BATCH_SIZE):
            result = await post_batch(client, tag, keys[start : start + BATCH_SIZE], category)
            data = result.get("data", {})
            success += data.get("success", 0)
            skipped += data.get("skipped_dup", 0)
            failed += data.get("failed", 0)

    return {
        "ok": True,
        "total": len(keys),
        "success": success,
        "skipped": skipped,
        "invalid": invalid,
        "failed": failed,
    }


STATUS_MAP: Final = {1: "开启", 2: "停用"}


def usage_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        key = item.get("key_masked") or "-"
        usage = item.get("used_quota", 0)
        status = STATUS_MAP.get(item.get("status", 0), "-")
        created_raw = item.get("created_at", "")
        created_at = created_raw[:19].replace("T", " ") if created_raw else "-"
        rows.append(
            {
                "key": str(key),
                "usage": usage,
                "status": status,
                "created_at": created_at,
            }
        )
    return rows


async def fetch_usage() -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    page = 1
    rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        while True:
            body: dict[str, Any] | None = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    response = await client.get(
                        f"{BASE_URL}/channels",
                        headers=headers,
                        params={"page": page, "page_size": 200},
                        timeout=TIMEOUT,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < MAX_RETRIES:
                            await anyio.sleep(2 ** (attempt + 1))
                            continue
                    body = response.json()
                    break
                except (httpx.RequestError, ValueError) as exc:
                    if attempt >= MAX_RETRIES:
                        raise RuntimeError("加载用量失败") from exc
                    await anyio.sleep(2 ** (attempt + 1))
            if body is None:
                raise RuntimeError("加载用量失败")
            if body.get("code") != 0:
                return {"ok": False, "error": "加载用量失败"}
            data = body.get("data", {})
            items = data.get("items", [])
            rows.extend(usage_rows(items))
            total = data.get("total", len(rows))
            if not items or len(rows) >= total:
                break
            page += 1

    return {"ok": True, "items": rows}


app = FastAPI(title="Key Usage")

OPEN_PATHS = {"/login", "/logout"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in OPEN_PATHS or path.startswith("/login"):
        return await call_next(request)
    if not request.session.get("authed"):
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "未登录"}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")


@app.get("/login", response_class=HTMLResponse)
async def login_page() -> str:
    return LOGIN_PAGE


@app.post("/login")
async def login_submit(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "请求格式错误"}, status_code=400)
    user = str(body.get("username", ""))
    pwd = str(body.get("password", ""))
    if hmac.compare_digest(user, USERNAME) and hmac.compare_digest(pwd, PASSWORD):
        request.session["authed"] = True
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "账号或密码错误"}, status_code=401)


@app.post("/logout")
async def logout(request: Request) -> JSONResponse:
    request.session.clear()
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    return HTML_PAGE


@app.post("/api/upload")
async def upload(request: UploadRequest) -> JSONResponse:
    if not API_KEY:
        return JSONResponse(
            {"ok": False, "error": "请设置 MSK_API_KEY 环境变量"}, status_code=503
        )
    if request.category not in CATEGORIES:
        return JSONResponse(
            {"ok": False, "error": "不支持的分类"}, status_code=400
        )
    try:
        result = await upload_keys(request.keys_text, request.category)
    except (httpx.RequestError, PermissionError, RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    return JSONResponse(result)


@app.get("/api/usage")
async def usage() -> JSONResponse:
    if not API_KEY:
        return JSONResponse(
            {"ok": False, "error": "请设置 MSK_API_KEY 环境变量"}, status_code=503
        )
    try:
        result = await fetch_usage()
    except (httpx.RequestError, RuntimeError, ValueError):
        return JSONResponse({"ok": False, "error": "加载用量失败"}, status_code=502)
    return JSONResponse(result)


LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>登录</title>
<style>
:root{--bg1:#eef2ff;--bg2:#f8fafc;--bg3:#faf5ff;--surface:rgba(255,255,255,.72);--text:#1e1b2e;--muted:#6b6786;--border:rgba(99,102,241,.12);--accent:#6366f1;--accent2:#8b5cf6;--error:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;color:var(--text);background:linear-gradient(135deg,var(--bg1) 0%,var(--bg2) 50%,var(--bg3) 100%);position:relative;overflow:hidden}
body::before{content:'';position:absolute;top:-30%;left:-10%;width:60%;height:80%;background:radial-gradient(ellipse,rgba(99,102,241,.12),transparent 60%);filter:blur(40px);animation:float1 12s ease-in-out infinite}
body::after{content:'';position:absolute;bottom:-30%;right:-10%;width:55%;height:70%;background:radial-gradient(ellipse,rgba(139,92,246,.1),transparent 60%);filter:blur(40px);animation:float2 14s ease-in-out infinite}
@keyframes float1{50%{transform:translate(40px,30px)}}
@keyframes float2{50%{transform:translate(-30px,-20px)}}
.card{position:relative;width:min(380px,calc(100% - 40px));background:var(--surface);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border:1px solid var(--border);border-radius:20px;padding:40px 36px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 12px 40px rgba(99,102,241,.08),0 4px 12px rgba(0,0,0,.03);animation:cardIn .6s cubic-bezier(.16,1,.3,1)}
@keyframes cardIn{from{opacity:0;transform:translateY(24px) scale(.96)}to{opacity:1;transform:translateY(0) scale(1)}}
.logo{width:48px;height:48px;margin:0 auto 20px;border-radius:14px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;box-shadow:0 8px 24px rgba(99,102,241,.25);animation:cardIn .6s .1s cubic-bezier(.16,1,.3,1) backwards}
.logo svg{width:24px;height:24px;color:#fff}
h1{margin-bottom:6px;font-size:22px;font-weight:700;letter-spacing:-.02em;text-align:center}
.subtitle{text-align:center;color:var(--muted);font-size:13px;margin-bottom:32px}
label{display:block;margin-bottom:6px;font-size:12px;font-weight:600;color:var(--muted);letter-spacing:.02em;text-transform:uppercase}
input{width:100%;margin-bottom:22px;padding:13px 16px;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.6);color:var(--text);font:14px inherit;transition:all .2s}
input:focus{outline:0;border-color:var(--accent);background:rgba(255,255,255,.9);box-shadow:0 0 0 4px rgba(99,102,241,.1)}
button{width:100%;min-height:46px;border:0;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font:600 15px inherit;cursor:pointer;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 14px rgba(99,102,241,.3)}
button:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(99,102,241,.35)}
button:active{transform:translateY(0)}
.feedback{min-height:22px;margin-top:14px;color:var(--error);font-size:13px;text-align:center}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div class="card">
  <div class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></div>
  <h1>欢迎回来</h1>
  <p class="subtitle">请登录以管理您的 Key</p>
  <form id="form">
    <label for="username">账号</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">密码</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">登 录</button>
    <div id="feedback" class="feedback" role="alert"></div>
  </form>
</div>
<script>
document.getElementById('form').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const fb=document.getElementById('feedback');
  fb.textContent='';
  try{
    const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value,password:document.getElementById('password').value})});
    const d=await r.json();
    if(d.ok){window.location.href='/';}else{fb.textContent=d.error||'登录失败';}
  }catch(err){fb.textContent='网络错误';}
});
</script>
</body>
</html>"""


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="上传 Key 并查看用量">
<link rel="icon" href="data:,">
<title>Key 管理</title>
<style>
:root{--bg1:#eef2ff;--bg2:#f8fafc;--bg3:#faf5ff;--surface:rgba(255,255,255,.72);--surface-solid:#fff;--text:#1e1b2e;--muted:#6b6786;--border:rgba(99,102,241,.12);--accent:#6366f1;--accent2:#8b5cf6;--hover:#4f46e5;--success:#10b981;--success-bg:rgba(16,185,129,.1);--error:#ef4444;--error-bg:rgba(239,68,68,.1)}
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;color:var(--text);background:linear-gradient(135deg,var(--bg1) 0%,var(--bg2) 50%,var(--bg3) 100%);position:relative;overflow-x:hidden}
body::before{content:'';position:fixed;top:-20%;left:-10%;width:50%;height:60%;background:radial-gradient(ellipse,rgba(99,102,241,.1),transparent 60%);filter:blur(60px);pointer-events:none;animation:float1 14s ease-in-out infinite}
body::after{content:'';position:fixed;bottom:-20%;right:-10%;width:50%;height:60%;background:radial-gradient(ellipse,rgba(139,92,246,.08),transparent 60%);filter:blur(60px);pointer-events:none;animation:float2 16s ease-in-out infinite}
@keyframes float1{50%{transform:translate(30px,20px)}}
@keyframes float2{50%{transform:translate(-20px,-15px)}}
main{position:relative;width:min(820px,calc(100% - 48px));margin:0 auto;padding:56px 0 80px}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:44px;animation:slideUp .6s cubic-bezier(.16,1,.3,1)}
h1{font-size:28px;font-weight:700;letter-spacing:-.025em;background:linear-gradient(135deg,var(--text),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.logout{padding:8px 18px;border:1px solid var(--border);border-radius:10px;background:var(--surface);backdrop-filter:blur(12px);color:var(--muted);font:500 13px inherit;cursor:pointer;transition:all .2s}
.logout:hover{border-color:var(--accent);color:var(--accent);box-shadow:0 4px 12px rgba(99,102,241,.12)}
section{animation:slideUp .6s cubic-bezier(.16,1,.3,1) backwards}
section:nth-child(2){animation-delay:.1s}
section+section{margin-top:36px}
@keyframes slideUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
.card{background:var(--surface);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border:1px solid var(--border);border-radius:16px;padding:28px 28px 24px;box-shadow:0 1px 2px rgba(0,0,0,.03),0 8px 32px rgba(99,102,241,.06),0 2px 8px rgba(0,0,0,.02)}
.heading{margin-bottom:16px}
h2{font-size:16px;font-weight:600;letter-spacing:-.01em}
label{display:block;margin-bottom:8px;font-size:12px;font-weight:600;color:var(--muted);letter-spacing:.03em;text-transform:uppercase}
select{display:block;width:100%;max-width:280px;margin-bottom:14px;padding:11px 14px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.6);color:var(--text);font:14px inherit;cursor:pointer;transition:all .2s}
select:focus{outline:0;border-color:var(--accent);background:rgba(255,255,255,.9);box-shadow:0 0 0 4px rgba(99,102,241,.1)}
textarea{display:block;width:100%;min-height:170px;padding:14px 16px;resize:vertical;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.6);color:var(--text);font:13px/1.7 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;transition:all .2s}
textarea:focus{outline:0;border-color:var(--accent);background:rgba(255,255,255,.9);box-shadow:0 0 0 4px rgba(99,102,241,.1)}
textarea::placeholder{color:var(--muted);opacity:.6}
.actions{display:flex;align-items:center;gap:14px;margin-top:16px}
button{min-height:44px;padding:0 26px;border:0;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font:600 14px inherit;cursor:pointer;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 14px rgba(99,102,241,.25)}
button:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(99,102,241,.3)}
button:active{transform:translateY(0)}
button:focus-visible{outline:3px solid rgba(99,102,241,.3);outline-offset:2px}
button:disabled{cursor:wait;opacity:.6;transform:none}
.feedback{min-height:22px;color:var(--muted);font-size:13px}
.feedback.success{color:var(--success);font-weight:500}
.feedback.error{color:var(--error);font-weight:500}
.list{overflow:hidden;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.55);backdrop-filter:blur(16px);box-shadow:0 4px 24px rgba(99,102,241,.05)}
table{width:100%;border-collapse:collapse}
th,td{padding:14px 18px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-size:11px;font-weight:600;background:rgba(99,102,241,.04);text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
th:nth-last-child(-n+2),td:nth-last-child(-n+2){text-align:right}
tbody tr{transition:background .15s}
tbody tr:hover{background:rgba(99,102,241,.03)}
tbody tr:last-child td{border-bottom:0}
td:first-child{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
td:nth-last-child(-n+2){font-variant-numeric:tabular-nums}
.badge{display:inline-flex;align-items:center;gap:5px;padding:3px 12px;border-radius:20px;font-size:12px;font-weight:600}
.badge::before{content:'';width:6px;height:6px;border-radius:50%}
.badge-on{background:var(--success-bg);color:var(--success)}
.badge-on::before{background:var(--success);box-shadow:0 0 6px rgba(16,185,129,.5)}
.badge-off{background:var(--error-bg);color:var(--error)}
.badge-off::before{background:var(--error);box-shadow:0 0 6px rgba(239,68,68,.4)}
.state{padding:48px 16px;text-align:center;color:var(--muted);font-size:14px}
.spinner{display:inline-block;width:14px;height:14px;margin-right:8px;vertical-align:-2px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:640px){main{width:min(100% - 32px,820px);padding-top:36px}h1{font-size:22px}th,td{padding:12px 10px;font-size:12px}td:first-child{font-size:12px}.card{padding:20px 18px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<main>
  <div class="topbar">
    <h1>Key 管理</h1>
    <button id="logout" class="logout" type="button">退出登录</button>
  </div>
  <section>
    <div class="card">
      <label for="category">上传 Key</label>
      <select id="category">
        <option value="anthropic">Anthropic</option>
        <option value="anthropic_small">Anthropic (小额度)</option>
        <option value="openai" selected>OpenAI</option>
        <option value="aws">AWS</option>
        <option value="azure">Azure</option>
        <option value="ai_studio">AI Studio</option>
      </select>
      <textarea id="keys" placeholder="每行一个 Key" spellcheck="false"></textarea>
      <div class="actions">
        <button id="upload" type="button">上传</button>
        <span id="feedback" class="feedback" role="status" aria-live="polite"></span>
      </div>
    </div>
  </section>
  <section>
    <div class="heading"><h2>用量</h2></div>
    <div id="usage" class="list"><div class="state">加载中…</div></div>
  </section>
</main>
<script>
const keys=document.getElementById('keys');
const category=document.getElementById('category');
const upload=document.getElementById('upload');
const feedback=document.getElementById('feedback');
const usage=document.getElementById('usage');
const placeholders={
  anthropic:'每行一个 sk-ant-... Key',
  anthropic_small:'每行一个 sk-ant-... Key (小额度)',
  openai:'每行一个 sk-... Key',
  aws:'每行一个 AccessKeyID|SecretAccessKey',
  azure:'每行一个 Endpoint|ApiKey|ApiVersion',
  ai_studio:'每行一个 Key'
};
category.addEventListener('change',()=>{keys.placeholder=placeholders[category.value]||'每行一个 Key';});
keys.placeholder=placeholders[category.value];
const escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const formatNum=value=>typeof value==='number'?value.toLocaleString():escapeHtml(value??0);
const badge=status=>{const cls=status==='开启'?'badge-on':status==='停用'?'badge-off':'';return cls?'<span class="badge '+cls+'">'+escapeHtml(status)+'</span>':'<span>'+escapeHtml(status)+'</span>';};

async function loadUsage(){
  usage.innerHTML='<div class="state">加载中…</div>';
  try{
    const response=await fetch('/api/usage');
    const data=await response.json();
    if(!data.ok)throw new Error(data.error||'加载失败');
    if(!data.items.length){usage.innerHTML='<div class="state">暂无数据</div>';return;}
    usage.innerHTML='<table><thead><tr><th>Key</th><th>用量</th><th>状态</th><th>创建时间</th></tr></thead><tbody>'+data.items.map(item=>'<tr><td>'+escapeHtml(item.key)+'</td><td>'+formatNum(item.usage)+'</td><td>'+badge(item.status)+'</td><td>'+escapeHtml(item.created_at)+'</td></tr>').join('')+'</tbody></table>';
  }catch(error){usage.innerHTML='<div class="state">'+escapeHtml(error.message)+'</div>';}
}

upload.addEventListener('click',async()=>{
  feedback.className='feedback';
  feedback.textContent='';
  upload.disabled=true;
  upload.innerHTML='<span class="spinner"></span>上传中';
  try{
    const response=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:category.value,keys_text:keys.value})});
    const data=await response.json();
    if(!data.ok)throw new Error(data.error||'上传失败');
    feedback.className='feedback success';
    feedback.textContent='已上传 '+data.success+' 个 Key';
    keys.value='';
    await loadUsage();
  }catch(error){feedback.className='feedback error';feedback.textContent=error.message;}
  finally{upload.disabled=false;upload.textContent='上传';}
});

document.getElementById('logout').addEventListener('click',async()=>{
  await fetch('/logout',{method:'POST'});
  window.location.href='/login';
});

loadUsage();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=20000)
