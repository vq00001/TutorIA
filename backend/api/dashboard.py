from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from backend.api.common import connection, now
from backend.db.connection import one, rows

router = APIRouter()


@router.get("/dashboard/summary")
def summary(conn=Depends(connection, scope="function")):
    user = one(conn, "SELECT timezone FROM app.users WHERE id=auth.uid()")
    zone = ZoneInfo(user["timezone"])
    today = now().astimezone(zone).date()
    monday = today - timedelta(days=today.weekday())
    intervals = rows(conn, "SELECT start_at,end_at FROM app.study_intervals ORDER BY start_at")
    totals = {}
    for r in intervals:
        start, end = r["start_at"], r["end_at"]
        while start < end:
            day = start.astimezone(zone).date()
            boundary = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(
                timezone.utc
            )
            stop = min(end, boundary)
            totals[day] = totals.get(day, 0) + (stop - start).total_seconds()
            start = stop
    days = [totals.get(monday + timedelta(days=i), 0) / 3600 for i in range(7)]
    streak = 0
    check = today if totals.get(today, 0) >= 300 else today - timedelta(days=1)
    while totals.get(check, 0) >= 300:
        streak += 1
        check -= timedelta(days=1)
    mastered = one(
        conn,
        """SELECT count(*) n FROM app.concept_mastery m JOIN app.concepts c ON c.id=m.concept_id
       JOIN app.courses co ON co.id=m.course_id WHERE m.level='mastered' AND c.status='approved' AND co.status='active'""",
    )["n"]
    cutoff = datetime.combine(monday, time(), zone).astimezone(timezone.utc)
    previous = one(
        conn,
        """SELECT count(*) n FROM (SELECT e.concept_id FROM app.mastery_events e
      JOIN app.concepts c ON c.id=e.concept_id JOIN app.courses co ON co.id=e.course_id
      WHERE e.valid AND e.created_at<:cutoff AND c.status='approved' AND co.status='active'
      GROUP BY e.concept_id HAVING count(*)>=3 AND (1+sum(weight*value))/(2+sum(weight))>=.8) prior""",
        cutoff=cutoff,
    )["n"]
    route = one(
        conn,
        """SELECT r.id,r.course_id FROM app.roadmaps r WHERE r.status='active'
      ORDER BY r.created_at DESC,r.id LIMIT 1""",
    )
    steps = (
        rows(
            conn,
            "SELECT state,estimated_minutes FROM app.roadmap_steps WHERE roadmap_id=:id ORDER BY position",
            id=route["id"],
        )
        if route
        else []
    )
    next_step = next((s for s in steps if s["state"] not in ("completed",)), None)
    return {
        "data": dict(
            studyHours=sum(days),
            studyHoursPeriod=f"{monday.isoformat()} / {(monday + timedelta(days=6)).isoformat()}",
            studyHoursByDay=days,
            streakDays=streak,
            masteredConcepts=mastered,
            masteredConceptsChange=mastered - previous,
            roadmapStep=sum(s["state"] == "completed" for s in steps),
            roadmapTotal=len(steps),
            nextSessionMinutes=next_step["estimated_minutes"]
            if next_step and next_step["state"] == "pending"
            else 0,
            nextCourseId=route["course_id"]
            if route and next_step and next_step["state"] == "pending"
            else None,
            timezone=user["timezone"],
        )
    }
