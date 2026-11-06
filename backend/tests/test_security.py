from backend.security.tokens import digest, passwords, token, verify_password


def test_session_tokens_are_random_and_only_hashes_match():
    raw, a = token()
    other, b = token()
    assert len(a) == 32 and raw != other and a != b
    assert digest(raw) == a


def test_argon2id_hash_and_wrong_password():
    encoded = passwords.hash("correct horse battery staple")
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "correct horse battery staple")
    assert not verify_password(encoded, "incorrect")
    assert not verify_password("invalid hash", "correct horse battery staple")
