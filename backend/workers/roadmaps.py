from backend.db.connection import one, transaction
from backend.services.roadmaps import build_roadmap


def refresh_roadmap(job, lease):
    with transaction(job["requested_by"]) as conn:
        goal = one(conn, "SELECT status FROM app.learning_goals WHERE id=:id", id=job["target_id"])
        if not goal or goal["status"] != "active":
            lease.complete(conn)
            return
        build_roadmap(conn, job["target_id"], job["requested_by"], validate=lease.validate)
        lease.complete(conn)
