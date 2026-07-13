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
    category: str = "anthropic"
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
:root{--canvas:#f7f7f5;--surface:#fff;--text:#20201e;--muted:#77746f;--border:#e5e3df;--accent:#1769d2;--hover:#1058b6;--error:#b54435}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{width:min(360px,calc(100% - 48px));background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:36px 32px}
h1{margin:0 0 28px;font-size:20px;font-weight:650;text-align:center}
label{display:block;margin-bottom:6px;font-size:13px;font-weight:500}
input{display:block;width:100%;margin-bottom:18px;padding:11px 14px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font:14px inherit;transition:border-color .15s,box-shadow .15s}
input:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px rgba(23,105,210,.12)}
button{width:100%;min-height:44px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:600 14px inherit;cursor:pointer;transition:background .15s}
button:hover{background:var(--hover)}
.feedback{min-height:20px;margin-top:12px;color:var(--error);font-size:13px;text-align:center}
</style>
</head>
<body>
<div class="card">
  <h1>登录</h1>
  <form id="form">
    <label for="username">账号</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">密码</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">登录</button>
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
<title>Key 用量</title>
<style>
:root{--canvas:#f7f7f5;--surface:#fff;--text:#20201e;--muted:#77746f;--border:#e5e3df;--accent:#1769d2;--hover:#1058b6;--success:#237a42;--error:#b54435}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
main{width:min(720px,calc(100% - 48px));margin:0 auto;padding:56px 0 80px}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:40px}
h1{margin:0;font-size:24px;font-weight:650;letter-spacing:-.02em}
.logout{padding:6px 14px;border:1px solid var(--border);border-radius:7px;background:var(--surface);color:var(--muted);font:500 13px inherit;cursor:pointer;transition:border-color .15s,color .15s}
.logout:hover{border-color:var(--accent);color:var(--accent)}
section+section{margin-top:40px}
.heading{margin-bottom:12px}
h2{margin:0;font-size:15px;font-weight:600}
label{display:block;margin-bottom:8px;font-size:13px;font-weight:500}
select{display:block;width:100%;max-width:260px;margin-bottom:12px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font:13px inherit;cursor:pointer;transition:border-color .15s,box-shadow .15s}
select:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px rgba(23,105,210,.12)}
textarea{display:block;width:100%;min-height:180px;padding:14px 16px;resize:vertical;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font:13px/1.7 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;transition:border-color .15s,box-shadow .15s}
textarea:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px rgba(23,105,210,.12)}
.actions{display:flex;align-items:center;gap:12px;margin-top:12px}
button{min-height:44px;padding:0 20px;border:0;border-radius:7px;background:var(--accent);color:#fff;font:600 14px inherit;cursor:pointer;transition:background .15s,opacity .15s}
button:hover{background:var(--hover)}
button:focus-visible{outline:3px solid rgba(23,105,210,.25);outline-offset:2px}
button:disabled{cursor:wait;opacity:.55}
.feedback{min-height:22px;color:var(--muted)}
.feedback.success{color:var(--success)}
.feedback.error{color:var(--error)}
.list{overflow:hidden;border:1px solid var(--border);border-radius:8px;background:var(--surface)}
table{width:100%;border-collapse:collapse}
th,td{padding:13px 16px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-size:12px;font-weight:500;background:#fbfbfa;white-space:nowrap}
th:nth-last-child(-n+2),td:nth-last-child(-n+2){text-align:right}
tbody tr:last-child td{border-bottom:0}
td:first-child{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
td:nth-last-child(-n+2){font-variant-numeric:tabular-nums}
.status-on{color:var(--success);font-weight:500}
.status-off{color:var(--error);font-weight:500}
.state{padding:36px 16px;text-align:center;color:var(--muted)}
.spinner{display:inline-block;width:13px;height:13px;margin-right:8px;vertical-align:-2px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:520px){main{width:min(100% - 32px,720px);padding-top:32px}h1{margin-bottom:32px}th,td{padding:12px}}
@media(prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;transition-duration:.01ms!important}}
</style>
</head>
<body>
<main>
  <div class="topbar"><h1>Key 用量</h1><button id="logout" class="logout" type="button">退出</button></div>
  <section>
    <label for="category">上传 Key</label>
    <select id="category">
      <option value="anthropic">Anthropic</option>
      <option value="anthropic_small">Anthropic (小额度)</option>
      <option value="openai">OpenAI</option>
      <option value="aws">AWS</option>
      <option value="azure">Azure</option>
      <option value="ai_studio">AI Studio</option>
    </select>
    <textarea id="keys" placeholder="每行一个 Key" spellcheck="false"></textarea>
    <div class="actions">
      <button id="upload" type="button">上传</button>
      <span id="feedback" class="feedback" role="status" aria-live="polite"></span>
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
const statusClass=value=>value==='开启'?'status-on':value==='停用'?'status-off':'';

async function loadUsage(){
  usage.innerHTML='<div class="state">加载中…</div>';
  try{
    const response=await fetch('/api/usage');
    const data=await response.json();
    if(!data.ok)throw new Error(data.error||'加载失败');
    if(!data.items.length){usage.innerHTML='<div class="state">暂无数据</div>';return;}
    usage.innerHTML='<table><thead><tr><th>Key</th><th>用量</th><th>状态</th><th>创建时间</th></tr></thead><tbody>'+data.items.map(item=>'<tr><td>'+escapeHtml(item.key)+'</td><td>'+formatNum(item.usage)+'</td><td class="'+statusClass(item.status)+'">'+escapeHtml(item.status)+'</td><td>'+escapeHtml(item.created_at)+'</td></tr>').join('')+'</tbody></table>';
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

    uvicorn.run(app, host="127.0.0.1", port=20000)
