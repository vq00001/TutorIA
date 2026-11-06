import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from backend.api.serialization import timestamp, utc_endpoint, wire_result
from starlette.responses import Response


def test_timestamps_are_utc_but_source_text_is_preserved():
    source = "2026-11-06T00:00:00+00:00"
    result = wire_result(
        {"at": datetime(2026, 11, 6, 1, tzinfo=timezone(timedelta(hours=-3))), "source": source}
    )
    assert result == {"at": "2026-11-06T04:00:00Z", "source": source}


def test_naive_timestamp_is_not_silently_assigned_a_timezone():
    with pytest.raises(ValueError, match="Naive datetime"):
        timestamp(datetime(2026, 11, 6))


def test_http_response_is_not_reencoded():
    response = Response(b"synthetic original bytes", media_type="text/plain")
    assert wire_result(response) is response


def test_sync_and_async_endpoint_signatures_and_encoding():
    def sync(value: int):
        return {"value": value, "at": datetime(2026, 11, 6, tzinfo=timezone.utc)}

    async def async_endpoint(value: int):
        return sync(value)

    assert utc_endpoint(sync)(3)["at"].endswith("Z")
    assert asyncio.run(utc_endpoint(async_endpoint)(3))["value"] == 3
