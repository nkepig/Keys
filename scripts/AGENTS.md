# SCRIPTS KNOWLEDGE BASE

**Scope**: Standalone scrapers, verifiers, and monitors under `scripts/`.

## OVERVIEW

10 standalone scripts that collect API keys from public sources and verify/synchronize them. Each script is runnable independently via `uv run python scripts/<name>.py`.

## STRUCTURE

```
scripts/
├── pastebin_scraper.py          # largest (~460L), Pastebin + Google search combo
├── kaggle_scraper.py            # Kaggle notebooks
├── fofa_scraper.py              # FOFA API search
├── hf_google_scraper.py         # HuggingFace → Google keys
├── hf_openai_scraper.py         # HuggingFace → OpenAI keys
├── hf_claude_scraper.py         # HuggingFace → Anthropic keys
├── verify_keys.py               # Re-verify all existing DB keys
├── export_keys_to_remote.py     # Push keys to remote endpoint
├── newapi_channel_monitor.py    # Monitor NewAPI channel balance
└── newapi_site_monitor.py       # Monitor NewAPI site health (cron-friendly)
```

## WHERE TO LOOK

| Task | Script | Notes |
|------|--------|-------|
| Add new scrape source | Create `*_scraper.py` | Follow `hf_*` or `pastebin` pattern: parse → verify → save via `key_service.batch_process_keys()` |
| Re-verify DB keys | `verify_keys.py` | Iterates all keys, re-runs LLM verify, updates `status_code` |
| Push keys elsewhere | `export_keys_to_remote.py` | Reads DB, optionally filters by provider/tier |
| Monitor external API | `newapi_site_monitor.py` / `newapi_channel_monitor.py` | SMTP alerting baked in; cron-friendly |
| Update regex patterns | Not here — go to `app/services/key_service.py:REGEX_RULES` | Scrapers only extract raw text; detection is centralized |

## CONVENTIONS

- **Executable directly**: No `__init__.py` in `scripts/`, but they can `from app.services.key_service import ...` because working directory is project root.
- **Shared pipeline**: After extraction, almost all scrapers call `key_service.batch_process_keys(...)` which handles dedup + LLM verification + persistence.
- **Logging**: Use `loguru` logger; scripts print progress lines.

## ANTI-PATTERNS

- **DO NOT** duplicate regex detection logic — keep raw extraction in scraper, but call `detect_provider_and_key()` or `detect_all_keys_from_text()` from `key_service`.
- **DO NOT** commit secrets / cached auth files (`data/newapi_auth_*.json`) — already in `.gitignore`.
- **NEVER** skip `batch_process_keys()` and insert raw keys directly to DB — verification + normalization happens inside that function.
