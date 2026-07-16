from aiohttp import ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.services.yunwu_upload_service import (
    CATEGORIES,
    UploadParameterError,
    UploadPermissionError,
    UploadServiceError,
    fetch_channels,
    upload_keys,
)
from app.services.yunwu_service import get_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
api_router = APIRouter(prefix="/api/yunwu", tags=["api"])
templates = Jinja2Templates(directory="templates")


class MSKUploadRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str = "openai"
    keys_text: str


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard/index.html", {})


@api_router.get("/gap")
async def yunwu_model_gap():
    svc = get_service()
    if not svc.username or not svc.password:
        raise HTTPException(status_code=400, detail="未配置云雾账号（YUNWU_USERNAME / YUNWU_PASSWORD）")
    try:
        return await svc.get_model_gap()
    except (ClientError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"获取模型缺口失败：{exc}") from exc


@api_router.post("/upload")
async def yunwu_upload(body: MSKUploadRequest):
    if not settings.msk_api_key:
        raise HTTPException(status_code=503, detail="请设置 MSK_API_KEY 环境变量")
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="不支持的分类")
    try:
        result = await upload_keys(body.keys_text, body.category)
    except UploadPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UploadParameterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "上传失败"))
    return result


@api_router.get("/channels")
async def yunwu_channels():
    """List keys already pushed via MSK OpenAPI /channels."""
    if not settings.msk_api_key:
        raise HTTPException(status_code=503, detail="请设置 MSK_API_KEY 环境变量")
    try:
        result = await fetch_channels()
    except UploadServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ClientError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"获取渠道列表失败：{exc}") from exc
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "加载渠道列表失败"))
    return result
