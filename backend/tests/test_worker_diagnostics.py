import json

from sqlalchemy.exc import ProgrammingError

from backend.workers.main import failure_diagnostic


def test_worker_diagnostic_exposes_category_and_sqlstate_without_sql_or_messages():
    class DatabaseError(Exception):
        sqlstate = "42501"

    secret = "PRIVATE_SOURCE_AND_TOKEN"
    error = ProgrammingError("SELECT " + secret, {"token": secret}, DatabaseError(secret))
    diagnostic = failure_diagnostic("generate_exam", error)
    assert diagnostic == {
        "event": "worker_failure",
        "kind": "generate_exam",
        "category": "ProgrammingError",
        "sqlState": "42501",
    }
    assert secret not in json.dumps(diagnostic)
    assert failure_diagnostic(secret, ValueError(secret))["kind"] == "unknown"
