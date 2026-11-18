"""Punto de entrada de PostgreSQL para el planificador externo."""

import json
from dataclasses import asdict, dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .connection import compose_database_url, require_restore_ready
from .jobs import recover_jobs
from .purge import purge_pending
from .retention import run_retention


@dataclass(frozen=True)
class MaintenanceResult:
    recovered_jobs: int
    purged_requests: int
    retention_ran: bool


def scheduler_tick(conn: Connection) -> MaintenanceResult:
    """Recupera trabajos, ejecuta purgas y reclama la retención del día UTC."""
    require_restore_ready(conn)
    conn.execute(sa.select(sa.func.pg_advisory_xact_lock(0x5455544D)))
    at = conn.scalar(sa.select(sa.func.clock_timestamp()))
    purged = purge_pending(conn)
    retention_ran = run_retention(conn, at=at)
    return MaintenanceResult(recover_jobs(conn), purged, retention_ran)


def main() -> None:
    engine = sa.create_engine(compose_database_url(), hide_parameters=True)
    try:
        with engine.begin() as conn:
            result = scheduler_tick(conn)
        print(json.dumps(asdict(result)))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
