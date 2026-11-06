import time

from sqlalchemy import text

from backend.db.connection import one, transaction


def main():
    while True:
        with transaction() as conn:
            if one(conn, "SELECT auth.restore_is_ready() AS ready")["ready"]:
                conn.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended('tutoria.scheduler',0))")
                )
                conn.execute(text("SELECT auth.scheduler_tick()"))
        time.sleep(15)


if __name__ == "__main__":
    main()
