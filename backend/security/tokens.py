from hashlib import sha256
from secrets import token_urlsafe

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def token():
    raw = token_urlsafe(32)
    return raw, sha256(raw.encode()).digest()


def digest(raw):
    return sha256(raw.encode()).digest()


def verify_password(encoded, password):
    try:
        return passwords.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
