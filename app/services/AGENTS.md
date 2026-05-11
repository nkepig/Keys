# SERVICES KNOWLEDGE BASE

**Scope**: Business logic inside `app/services/` and `app/services/llm/`.

## OVERVIEW

Core business layer: key detection/normalization, LLM verification, browser automation, URL scanning, and FOFA/NewAPI integrations.

## STRUCTURE

```
app/services/
├── key_service.py        # Key CRUD + provider detection regex + LLM verify dispatch
├── browser_service.py    # DrissionPage (Chromium) wrapper + Turnstile bypass + OCR
├── scanner_service.py    # Concurrent URL page fetch → extract keys via regex
├── fofa_service.py       # FOFA API search wrapper
├── newapi_service.py     # NewAPI auth + site/channel monitoring helpers
└── llm/
    ├── openai.py         # verify() via chat completions; fetch_models()
    ├── claude.py         # Anthropic verify/fetch_models
    ├── gemini.py         # Google verify/fetch_models
    └── openrouter.py     # OpenRouter verify/fetch_models
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add new LLM provider | `llm/` + `key_service.py:_VERIFY_SERVICES` | Add `verify()` + `fetch_models()` returning `{"status_code", "tier", "body"}` |
| Change tier mapping | `llm/<provider>.py:TIER_MAPPING` | Each provider maps its own rate-limit header or response field |
| Fix detection regex | `key_service.py:REGEX_RULES` + `_COMPILED_RULES` | Single source of truth for all provider key patterns |
| Batch verify / save | `key_service.py:batch_process_keys()` | Deduplicate → filter existing DB → verify → persist |
| Add browser automation | `browser_service.py` | Turnstile bypass is brittle; test changes on live challenge pages |
| Scan URL for keys | `scanner_service.py:scan_urls()` | Uses `ssl=False`, retries http↔https |
| Sync with FOFA | `fofa_service.py` | Uses `app.http_client.get_http_session()` |

## CONVENTIONS

- **Sync DB in thread**: `key_service.py` db_* functions are sync and wrapped with `asyncio.to_thread()` at call sites. Do not make them async.
- **Dict return**: LLM verify methods return plain `dict` (NOT Pydantic model). Keys: `status_code`, `tier`, `body`.
- **Exception classification**: `_classify_verify_exception()` maps aiohttp exceptions to stable string tags for the frontend.
- **Global aiohttp session**: FOFA/scanner use `get_http_session()`; OpenAI verify currently creates its own session because it needs a different timeout.

## ANTI-PATTERNS

- **DO NOT** add new provider regex in scrapers — centralize in `key_service.py`.
- **DO NOT** make DB functions async — they’re intentionally sync and offloaded via `to_thread`.
- **DO NOT** return Pydantic models from LLM verify — callers expect plain dict.
- **AVOID** making `browser_service.py` async unnecessarily — DrissionPage is sync-first.
