"""Unit tests for MSK channel list helpers."""

from app.services.yunwu_upload_service import channel_rows


def test_channel_rows_maps_usage_status_and_labels():
    rows = channel_rows(
        [
            {
                "key_masked": "sk-abc•••xyz",
                "used_quota": 500000,
                "status": 1,
                "category": "openai",
                "tag": "20260716-120000",
                "created_at": "2026-07-16T12:00:00Z",
            },
            {
                "key_masked": "sk-ant-•••",
                "used_quota": "0",
                "status": 2,
                "category": "anthropic",
                "tag": "user-batch",
                "created_at": "2026-07-15T08:30:11+08:00",
            },
            {
                "status": 99,
                "category": "unknown",
            },
        ]
    )
    assert rows[0]["key"] == "sk-abc•••xyz"
    assert rows[0]["usage"] == 1.0
    assert rows[0]["status"] == "开启"
    assert rows[0]["category_label"] == "OpenAI"
    assert rows[0]["created_at"] == "2026-07-16 12:00:00"

    assert rows[1]["status"] == "禁用"
    assert rows[1]["category_label"] == "Anthropic"
    assert rows[1]["created_at"] == "2026-07-15 08:30:11"

    assert rows[2]["key"] == "-"
    assert rows[2]["usage"] == 0.0
    assert rows[2]["status"] == "禁用"
    assert rows[2]["category_label"] == "unknown"
    assert rows[2]["created_at"] == "-"
