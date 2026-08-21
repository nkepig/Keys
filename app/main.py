import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# 兼容"直接运行 app/main.py"场景
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import init_db
from app.http_client import close_http_client, init_http_client
from app.routers import auth, key


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await init_http_client()
    yield
    await close_http_client()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def require_login_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/") or path in ("/login", "/favicon.ico"):
        return await call_next(request)
    if path == "/logout" and request.method == "POST":
        return await call_next(request)
    if not request.session.get("auth"):
        if path.startswith("/api"):
            return JSONResponse({"detail": "未登录"}, status_code=401)
        loc = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(url=f"/login?next={quote(loc, safe='')}", status_code=303)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=30 * 24 * 3600,
    same_site="lax",
    https_only=False,
)


# ── 全局异常处理器 ────────────────────────────────────────────────────────────
# 任何路由 raise HTTPException / raise Exception 均返回统一 JSON，
# 前端只需检查 res.ok 即可，无需每个 fetch 单独解析错误。

@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def general_exc_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc) or "服务器内部错误"},
    )


app.include_router(auth.router)
app.include_router(key.router)
app.include_router(key.api_router)


@app.get("/")
def index():
    return RedirectResponse(url="/keys")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8888, reload=True)
