"""Aggregate runtime states only; never export IDs or application content."""

import json
import os
from uuid import UUID

import psycopg

from tests.harness.cluster_guard import guard_connection


def main():
    control, run, nonce = guard_connection()
    with control:
        if control.execute("SELECT run_id::text,nonce FROM test_harness.run_guard").fetchall() != [
            (run, nonce)
        ]:
            raise RuntimeError("Cluster guard mismatch")
    database = "tutoria_test_" + UUID(run).hex + "_browser"
    if os.environ["TEST_DB_NAME"] != database:
        raise RuntimeError("Runtime database mismatch")
    with psycopg.connect(
        host="db-test", dbname=database, user="postgres", password=os.environ["TEST_DB_PASSWORD"]
    ) as conn:
        if conn.execute(
            "SELECT run_id::text,case_id,nonce FROM test_harness.run_guard"
        ).fetchall() != [(run, database, nonce)]:
            raise RuntimeError("Runtime guard mismatch")
        # Selected states are constrained enums and job kinds are allowlisted;
        # no prompts, filenames,
        # error messages, request IDs or user values are selected.
        result = {}
        for label, query in {
            "jobs": """SELECT CASE WHEN kind IN ('ingest_document','generate_chat','generate_exam',
                'grade_attempt','purge','extract_concepts','refresh_roadmap') THEN kind ELSE 'unknown' END,
                state,count(*) FROM app.jobs GROUP BY 1,2 ORDER BY 1,2""",
            "processing": "SELECT state,count(*) FROM app.processing_runs GROUP BY state ORDER BY state",
            "generations": "SELECT status,count(*) FROM app.generation_runs GROUP BY status ORDER BY status",
            "deletions": "SELECT state,count(*) FROM app.deletion_requests GROUP BY state ORDER BY state",
        }.items():
            result[label] = conn.execute(query).fetchall()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
