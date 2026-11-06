import os
from datetime import datetime, timezone

from sqlalchemy import text

from backend.db.connection import one, transaction
from backend.services.ledger import append_record, decode_key


def purge(job, lease):
    lease.check()
    with transaction() as conn:
        record = one(conn, "SELECT auth.purge_scope(:id) AS record", id=job["target_id"])["record"]
    # A failed external write leaves the SQL request running and is retried. Never
    # acknowledge completed merely because the SQL content transaction committed.
    record["completedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    append_record(os.environ["LEDGER_PATH"], decode_key(os.environ["LEDGER_KEY"]), record)
    with transaction() as conn:
        conn.execute(
            text("SELECT auth.finish_purge_job(:job,:lease,:request)"),
            dict(job=job["id"], lease=job["lease_token"], request=job["target_id"]),
        )
