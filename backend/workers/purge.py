import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from backend.db.connection import one, transaction


def purge(job, lease):
    lease.check()
    with transaction() as conn:
        record = one(conn, "SELECT auth.purge_document(:id) AS record", id=job["target_id"])[
            "record"
        ]
    # A separate encrypted ledger survives application backup restoration.
    # Repeated appends after a crash are idempotent by record id on replay.
    key = base64.b64decode(os.environ["LEDGER_KEY"], validate=True)
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(
        nonce, json.dumps(record, sort_keys=True).encode(), b"tutoria-ledger-v1"
    )
    directory = Path(os.environ["LEDGER_PATH"])
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (str(job["target_id"]) + ".ledger")).open("ab") as stream:
        stream.write(base64.b64encode(nonce + encrypted) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    with transaction(job["requested_by"]) as conn:
        lease.validate(conn)
        conn.execute(text("SELECT auth.confirm_purge(:id)"), dict(id=job["target_id"]))
        lease.complete(conn)
