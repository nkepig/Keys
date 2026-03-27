import secrets
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")


def _safe_next(raw: str | None) -> str:
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/keys"
    return raw


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("auth"):
        return RedirectResponse(url=_safe_next(request.query_params.get("next")), status_code=303)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"error": None, "next": request.query_params.get("next") or ""},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    password: str = Form(...),
    next: Optional[str] = Form(None),
):
    dest = _safe_next(next or request.query_params.get("next"))
    if request.session.get("auth"):
        return RedirectResponse(url=dest, status_code=303)
    if not secrets.compare_digest(password, settings.login_password):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "密码错误", "next": next or ""},
            status_code=401,
        )
    request.session["auth"] = True
    return RedirectResponse(url=dest, status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
