import json as _json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.services import key_service
from app.services.llm.claude import ClaudeService
from app.services.llm.gemini import GeminiService
from app.services.llm.openai import OpenAIService
from app.services.llm.openrouter import OpenRouterService

router = APIRouter(prefix="/keys", tags=["keys"])
api_router = APIRouter(prefix="/api", tags=["api"])
templates = Jinja2Templates(directory="templates")

PROVIDER_SERVICES = {
    "OpenAI": OpenAIService,
    "Anthropic": ClaudeService,
    "Google": GeminiService,
    "OpenRouter": OpenRouterService,
}


class KeyCreate(BaseModel):
    key: str
    origin: Optional[str] = None
    notes: Optional[str] = None


class KeyUpload(BaseModel):
    keys: str
    origin: Optional[str] = None
    concurrent: int = 10


class KeyUpdate(BaseModel):
    provider: Optional[str] = None
    key: Optional[str] = None
    origin: Optional[str] = None
    tier: Optional[str] = None
    models: Optional[str] = None
    status_code: Optional[int] = None
    notes: Optional[str] = None


@router.get("/", response_class=HTMLResponse)
def list_keys_page(request: Request):
    return templates.TemplateResponse(request, "key/list.html", {})


@api_router.get("/keys")
async def list_keys():
    return jsonable_encoder(await key_service.get_keys())


@api_router.post("/keys", status_code=201)
async def create_key(body: KeyCreate):
    results = await key_service.batch_process_keys([{"key": body.key, "origin": body.origin}])
    result = results[0] if results else {}
    if not result.get("saved"):
        raise HTTPException(status_code=400, detail=result.get("error", "保存失败"))
    return result


@api_router.post("/keys/upload")
async def upload_keys(body: KeyUpload):
    results = await key_service.batch_process_keys(
        body.keys, origin=body.origin, concurrent=body.concurrent
    )
    saved = sum(1 for r in results if r["saved"])
    return {"total": len(results), "saved": saved, "failed": len(results) - saved, "results": results}


@api_router.get("/keys/{key_id}")
async def get_key(key_id: int):
    key = await key_service.get_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Key 不存在")
    return jsonable_encoder(key)


@api_router.patch("/keys/{key_id}")
async def update_key(key_id: int, body: KeyUpdate):
    if not await key_service.get_key(key_id):
        raise HTTPException(status_code=404, detail="Key 不存在")
    # exclude_unset=True: 只更新请求中明确传入的字段（包括 null），允许清空备注等字段
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    results = await key_service.update_keys({"id": key_id, **updates})
    return jsonable_encoder(results[0])


@api_router.delete("/keys/{key_id}")
async def delete_key(key_id: int):
    if not (await key_service.delete_keys(key_id))[0]:
        raise HTTPException(status_code=404, detail="Key 不存在")
    return {"success": True}


@api_router.post("/keys/{key_id}/verify")
async def verify_key(key_id: int):
    key = await key_service.get_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Key 不存在")

    service = PROVIDER_SERVICES.get(key.provider)
    if not service:
        raise HTTPException(status_code=400, detail=f"不支持自动校验: {key.provider}")

    result = await service.verify(key.key)
    status_code = result["status_code"]
    tier_val = str(result["tier"]) if result["tier"] is not None else None
    verify_body = result.get("body")  # 原始响应体，透传给前端展示，不入库

    update_data: dict = {"id": key_id, "status_code": status_code}
    if tier_val is not None:
        update_data["tier"] = tier_val

    if status_code == 200:
        model_list = await key_service.fetch_models_for(key.provider, key.key)
        update_data["models"] = _json.dumps(model_list, ensure_ascii=False) if model_list else None
    else:
        # 非 200 时清除旧模型列表，避免残留
        update_data["models"] = None

    # update_keys 返回已持久化的最新 Key 对象，直接返回给前端
    # 前端无需再发第二个 GET，消除 SQLite 事务隔离竞争窗口
    updated_list = await key_service.update_keys(update_data)
    response_data = jsonable_encoder(updated_list[0])
    response_data["verify_body"] = verify_body  # 临时字段，仅本次响应携带
    return response_data
