from backend.security.tokens import passwords

# One hash at process initialization makes unknown and known account checks use
# exactly one expensive verification, without keeping a usable account secret.
DUMMY_HASH = passwords.hash("TutorIA unavailable account sentinel")
