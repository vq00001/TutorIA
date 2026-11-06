"""Provision/verify a marker only on the harness-owned isolated cluster."""

import json
import os
import re
import sys
from uuid import UUID

import psycopg


def guard_connection():
    run = str(UUID(os.environ["TEST_RUN_ID"]))
    nonce = os.environ["TEST_NONCE"]
    password = os.environ["TEST_DB_PASSWORD"]
    if (
        os.environ.get("TEST_DB_HOST") != "db-test"
        or not re.fullmatch("[a-f0-9]{64}", nonce)
        or not re.fullmatch("[a-f0-9]{64}", password)
    ):
        raise RuntimeError("Invalid isolated cluster guard configuration")
    return (
        psycopg.connect(host="db-test", dbname="postgres", user="postgres", password=password),
        run,
        nonce,
    )


def main():
    conn, run, nonce = guard_connection()
    with conn:
        if sys.argv[1:] == ["create"]:
            conn.execute("CREATE SCHEMA test_harness")
            conn.execute(
                "CREATE TABLE test_harness.run_guard(run_id uuid PRIMARY KEY,nonce text NOT NULL)"
            )
            conn.execute("INSERT INTO test_harness.run_guard VALUES(%s,%s)", (run, nonce))
        elif sys.argv[1:] != ["verify"]:
            raise RuntimeError("Expected create or verify")
        rows = conn.execute("SELECT run_id::text,nonce FROM test_harness.run_guard").fetchall()
        if rows != [(run, nonce)]:
            raise RuntimeError("Cluster guard mismatch")
        server = conn.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[
            0
        ]
    print(json.dumps({"runId": run, "serverId": server, "guard": "verified"}))


if __name__ == "__main__":
    main()
