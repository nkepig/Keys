# PROJECT KNOWLEDGE BASE

**Generated:** 2026-05-11
**Branch:** main

## OVERVIEW

Keys — FastAPI-based API key manager with scrapers (HuggingFace, Kaggle, FOFA, Pastebin, FOFA etc.) that collect, verify (via LLM APIs), and store keys in SQLite.

## STRUCTURE

```
Keys/
├── app/            # FastAPI app
│   ├── main.py
│   ├── config.py        # pydantic-settings (.env)
│   ├── db.py            # SQLModel engine + manual migration
│   ├── http_client.py   # global shared aiohttp session
│   ├── models/
│   │   └── key.py       # SQLModel Key table
│   ├── routers/
│   │   ├── key.py       # /keys page + /api/* endpoints
│   │   └── auth.py      # session login/logout
│   ├── services/        # business logic (see app/services/AGENTS.md)
│   │   ├── key_service.py
│   │   ├── browser_service.py
│   │   ├── scanner_service.py
│   │   ├── fofa_service.py
│   │   ├── newapi_service.py
│   │   └── llm/         # per-provider verify/fetch_models
│   └── utils/
│       ├── concurrency.py   # gather_limited semaphore helper
│       ├── scan_history.py
│       └── timezone.py      # UTC+8 timestamps
├── scripts/         # standalone scrapers/verifiers (see scripts/AGENTS.md)
├── static/js/       # vanilla JS (no build step)
├── templates/       # Jinja2 HTML
├── tests/
└── pyproject.toml   # uv, ruff, pre-commit
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add new scraper channel | `scripts/` | Standalone scripts, add then wire to router UI |
| Change DB schema | `app/models/key.py` + `app/db.py:_migrate()` | Migration is manual SQL in _migrate(), must be idempotent |
| Add new LLM provider | `app/services/llm/` | Add `verify()` + `fetch_models()` static methods returning `dict` |
| Change key regex rules | `app/services/key_service.py:REGEX_RULES` | Centralized; affects detection + storage |
| Fix concurrency / batching | `app/utils/concurrency.py:gather_limited` | Used across services and scripts |
| Add frontend page | `templates/` + `app/routers/key.py` | Jinja2 + vanilla JS, no build step |
| Change auth logic | `app/routers/auth.py` | Single-user session via itsdangerous |
| Change global middleware | `app/main.py` | `require_login_middleware` + exception handlers |

## CODE MAP

| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `app.main:app` | FastAPI | `app/main.py` | Entry point, lifespan, middleware |
| `Settings` | class | `app/config.py` | pydantic-settings from `.env` |
| `Key` | SQLModel | `app/models/key.py` | Core table (`keys`) |
| `batch_process_keys` | async def | `key_service.py` | Unify save/verify pipeline |
| `verify_key` | async def | `key_service.py` | LLM verification dispatch |
| `AutoBrowseService` | class | `browser_service.py` | DrissionPage + Turnstile bypass |
| `_migrate` | def | `app/db.py` | Idempotent SQLite schema migration |
| `get_http_session` | def | `app/http_client.py` | Shared aiohttp ClientSession getter |

## CONVENTIONS

- **Linter/Format**: ruff (pre-commit hook). No `__init__.py` anywhere (flat is fine).
- **Config**: pydantic-settings `BaseSettings`, `env_file=".env"`.
- **Logging**: `loguru` logger everywhere.
- **Dict interfaces**: LLM services return plain `dict` (`{"status_code", "tier", "body"}`), not Pydantic models.
- **HTTP Client**: Always get session from `app.http_client.get_http_session()`. Don't create new `ClientSession` unless standalone script.

## ANTI-PATTERNS (THIS PROJECT)

- **DO NOT** run sync DB operations in async context directly — wrap with `asyncio.to_thread(...)` (see `key_service.py` db_* functions).
- **DO NOT** DROP columns in `_migrate()` — keep backward compatibility (legacy table rename pattern already in use).
- **DO NOT** create per-request `aiohttp.ClientSession` in services — use the global one.
- **NEVER** store raw unmasked key in frontend JSON — `_mask_key()` produces `{key[:6]}•••{key[-4:]}`.
- **AVOID** adding heavy frontend build steps — templates are native Jinja2 + vanilla JS.

## COMMANDS

```bash
uv sync                          # install deps
uv run python -m app.main        # dev server @ :8888
uv run pytest                    # tests
uv run pre-commit install        # hooks (ruff)
uv run python scripts/<name>.py  # run standalone scraper/verifier
```

## NOTES

- Session middleware: `same_site="lax"`, `https_only=False` (dev-friendly).
- `main.py` lines 12-13: `__package__` guard allows `python app/main.py` direct run.
- `browser_service.py` embeds a JS extension manifest + script for Turnstile patch — modify with care.
- `db.py:_migrate()` handles legacy `scan_history` table rename; new devs should add new columns via the same PRAGMA-check pattern.
