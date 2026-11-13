import os

SECRET_KEY_JWT = os.getenv(
    "SECRET_KEY_JWT",
    "default_secret_key_with_minimum_length_of_32_characters_for_standard_rfc7518",
)
ALGORITHM = os.getenv("ALGORITHM", "HS256")

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
