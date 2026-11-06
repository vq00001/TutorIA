"""Encode typed timestamps at the HTTP boundary without rewriting source strings."""

from datetime import datetime, timezone
from functools import wraps
from inspect import iscoroutinefunction, signature

from fastapi.encoders import jsonable_encoder
from starlette.responses import Response


def timestamp(value: datetime):
    if value.tzinfo is None:
        raise ValueError("Naive datetime cannot cross the API boundary")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def wire_result(value):
    if isinstance(value, Response):
        return value
    return jsonable_encoder(value, custom_encoder={datetime: timestamp})


def utc_endpoint(endpoint):
    if iscoroutinefunction(endpoint):

        @wraps(endpoint)
        async def wrapped(*args, **kwargs):
            return wire_result(await endpoint(*args, **kwargs))
    else:

        @wraps(endpoint)
        def wrapped(*args, **kwargs):
            return wire_result(endpoint(*args, **kwargs))

    wrapped.__signature__ = signature(endpoint, eval_str=True)
    return wrapped
