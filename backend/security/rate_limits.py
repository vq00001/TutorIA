"""Durable limits with HMAC buckets: never store raw IP addresses or email addresses."""

import hmac
import os
from hashlib import sha256

from backend.api.common import ApiError
from backend.db.connection import one, transaction


def enforce(request, operation, email=None):
    secret = os.environ["LEDGER_KEY"].encode()
    host = request.client.host if request.client else "unknown"
    checks = [(f"{operation}:ip:{host}", 30)]
    if email:
        checks.append(
            (f"{operation}:email:{email.strip().lower()}", 10 if operation == "login" else 3)
        )
    allowed = True
    # Commit counts independently of a later rejected login/registration transaction.
    with transaction() as conn:
        for bucket, maximum in checks:
            key = hmac.new(secret, b"auth-limit-v1:" + bucket.encode(), sha256).digest()
            result = one(
                conn,
                "SELECT auth.take_rate_limit(:bucket,:maximum,900) AS allowed",
                bucket=key,
                maximum=maximum,
            )
            allowed = bool(result["allowed"]) and allowed
    if not allowed:
        raise ApiError("RATE_LIMIT_EXCEEDED", 429)
