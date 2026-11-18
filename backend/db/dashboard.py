"""Consultas de resumen y actividad personal."""

from datetime import date

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .audit import audit_events
from .study_sessions import study_intervals


def daily_study_seconds(conn: Connection, since: date):
    intervals = study_intervals.c
    day = sa.cast(sa.func.timezone("UTC", intervals.end_at), sa.Date)
    seconds = sa.func.sum(sa.func.extract("epoch", intervals.end_at - intervals.start_at))
    return conn.execute(
        sa.select(day, seconds)
        .where(intervals.user_id == sa.func.app.course_actor(), day >= since)
        .group_by(day)
    ).all()


def page_activity(conn: Connection, *, page: int, page_size: int, entity_type: str | None):
    event = audit_events.c
    visible = sa.select(
        event.id, event.course_id, event.action, event.entity_type, event.result, event.created_at
    ).where(event.actor_id == sa.func.app.course_actor(), event.source == "user")
    if entity_type is not None:
        visible = visible.where(event.entity_type == entity_type)
    visible = visible.cte("visible_activity")
    count = sa.select(sa.func.count().label("total")).select_from(visible).subquery()
    items = (
        sa.select(visible)
        .order_by(visible.c.created_at.desc(), visible.c.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .subquery()
    )
    rows = (
        conn.execute(
            sa.select(items, count.c.total)
            .select_from(count.outerjoin(items, sa.true()))
            .order_by(items.c.created_at.desc(), items.c.id.desc())
        )
        .mappings()
        .all()
    )
    return [row for row in rows if row.id is not None], rows[0].total
