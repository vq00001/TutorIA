import json
import re
import time
from datetime import datetime, timezone
from socket import gethostname
from threading import Event, Thread

from sqlalchemy import text

from backend.db.connection import one, transaction
from backend.workers.chat import generate_chat
from backend.workers.concepts import extract_concepts
from backend.workers.exams import generate_exam, grade_attempt
from backend.workers.ingest import ingest
from backend.workers.purge import purge
from backend.workers.roadmaps import refresh_roadmap


def failure_diagnostic(kind, exc):
    """Allowlisted diagnostics: never include provider/SQL exception messages."""
    category = type(exc).__name__
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return {
        "event": "worker_failure",
        "kind": kind
        if kind
        in {
            "ingest_document",
            "generate_chat",
            "generate_exam",
            "grade_attempt",
            "purge",
            "extract_concepts",
            "refresh_roadmap",
        }
        else "unknown",
        "category": category
        if category
        in {
            "ValueError",
            "ValidationError",
            "IntegrityError",
            "ProgrammingError",
            "OperationalError",
            "HTTPError",
            "ConnectionError",
            "ReadTimeout",
        }
        else "ProcessingError",
        "sqlState": sqlstate
        if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate)
        else None,
    }


class Lease:
    def __init__(self, job):
        self.job = job
        self.stop = Event()
        self.lost = Event()
        self.thread = Thread(target=self.heartbeat, daemon=True)

    def remaining(self):
        if self.job["deadline_at"]:
            remaining = (self.job["deadline_at"] - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise ValueError("DEADLINE_EXCEEDED")
            return remaining
        return 60

    def check(self):
        self.remaining()
        if self.lost.is_set():
            raise ValueError("LEASE_LOST")

    def heartbeat(self):
        while not self.stop.wait(30):
            try:
                with transaction(self.job["requested_by"]) as conn:
                    if self.job["kind"] == "purge":
                        renewed = one(
                            conn,
                            "SELECT auth.heartbeat_purge_job(:id,:token) AS renewed",
                            id=self.job["id"],
                            token=self.job["lease_token"],
                        )
                        if not renewed["renewed"]:
                            self.lost.set()
                            return
                        continue
                    row = one(
                        conn,
                        """UPDATE app.jobs SET lease_until=clock_timestamp()+interval '120 seconds'
                      WHERE id=:id AND state='running' AND lease_token=:token AND lease_until>clock_timestamp()
                      RETURNING id""",
                        id=self.job["id"],
                        token=self.job["lease_token"],
                    )
                    if not row:
                        self.lost.set()
                        return
            except Exception:
                self.lost.set()
                return

    def validate(self, conn):
        self.check()
        row = one(
            conn,
            """SELECT id FROM app.jobs WHERE id=:id AND state='running' AND lease_token=:token
            AND lease_until>clock_timestamp() AND (deadline_at IS NULL OR deadline_at>clock_timestamp()) FOR UPDATE""",
            id=self.job["id"],
            token=self.job["lease_token"],
        )
        if not row:
            raise ValueError("LEASE_LOST")

    def complete(self, conn):
        self.validate(conn)
        conn.execute(
            text(
                "UPDATE app.jobs SET state='succeeded',lease_until=NULL,lease_token=NULL WHERE id=:id"
            ),
            dict(id=self.job["id"]),
        )


def main():
    while True:
        with transaction() as conn:
            if one(conn, "SELECT auth.restore_is_ready() AS ready")["ready"]:
                conn.execute(text("SELECT auth.recover_jobs()"))
                job = one(conn, "SELECT * FROM auth.claim_job(:worker)", worker=gethostname())
            else:
                job = None
        if not job:
            time.sleep(1)
            continue
        lease = Lease(job)
        lease.thread.start()
        try:
            handlers = {
                "ingest_document": ingest,
                "generate_chat": generate_chat,
                "generate_exam": generate_exam,
                "grade_attempt": grade_attempt,
                "purge": purge,
                "extract_concepts": extract_concepts,
                "refresh_roadmap": refresh_roadmap,
            }
            if job["kind"] not in handlers:
                raise ValueError("UNKNOWN_JOB_KIND")
            handlers[job["kind"]](job, lease)
        except Exception as exc:
            print(json.dumps(failure_diagnostic(job["kind"], exc)), flush=True)
            code = (
                str(exc)
                if isinstance(exc, ValueError) and str(exc).isupper() and len(str(exc)) < 80
                else "PROCESSING_FAILED"
            )
            with transaction(job["requested_by"]) as conn:
                if job["kind"] == "purge":
                    conn.execute(
                        text("SELECT auth.fail_purge_job(:id,:token,:code)"),
                        dict(id=job["id"], token=job["lease_token"], code=code),
                    )
                    continue
                updated = one(
                    conn,
                    """UPDATE app.jobs SET state='failed',error_code=:code,lease_token=NULL,lease_until=NULL
                  WHERE id=:id AND lease_token=:token RETURNING id""",
                    id=job["id"],
                    token=job["lease_token"],
                    code=code,
                )
                if updated and job["kind"] == "ingest_document":
                    conn.execute(
                        text(
                            "UPDATE app.processing_runs SET state='failed',finished_at=now(),error_code=:code WHERE id=:id AND state<>'ready'"
                        ),
                        dict(id=job["target_id"], code=code),
                    )
                    conn.execute(
                        text("""UPDATE app.document_versions SET status='failed',error_code=:code
                      WHERE id=(SELECT version_id FROM app.processing_runs WHERE id=:id) AND status<>'ready'"""),
                        dict(id=job["target_id"], code=code),
                    )
                if updated and job["kind"] == "generate_chat":
                    conn.execute(
                        text(
                            "UPDATE app.generation_runs SET status='failed',finished_at=now(),error_code=:code WHERE id=:id AND status IN ('queued','running')"
                        ),
                        dict(id=job["target_id"], code=code),
                    )
                    conn.execute(
                        text(
                            "UPDATE app.messages SET status='failed',parts='[]' WHERE generation_id=:id AND status IN ('pending','streaming')"
                        ),
                        dict(id=job["target_id"]),
                    )
                if updated and job["kind"] == "generate_exam":
                    conn.execute(
                        text(
                            "UPDATE app.exams SET status='failed' WHERE id=:id AND status='generating'"
                        ),
                        dict(id=job["target_id"]),
                    )
                    conn.execute(
                        text(
                            "UPDATE app.generation_runs SET status='failed',error_code=:code,finished_at=now() WHERE id=(SELECT generation_id FROM app.exams WHERE id=:id)"
                        ),
                        dict(id=job["target_id"], code=code),
                    )
        finally:
            lease.stop.set()
            lease.thread.join(timeout=2)


if __name__ == "__main__":
    main()
