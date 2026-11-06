"""Technical expiry without impersonating a student."""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade():
    bind = op.get_bind().execution_options(no_parameters=True)
    bind.exec_driver_sql("""
    CREATE FUNCTION auth.scheduler_tick() RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE a app.exam_attempts;
    BEGIN
      PERFORM auth.recover_jobs();
      FOR a IN SELECT * FROM app.exam_attempts WHERE status='in_progress' AND deadline_at<=clock_timestamp()
        ORDER BY course_id,id LOOP
        PERFORM 1 FROM app.courses WHERE id=a.course_id FOR UPDATE;
        PERFORM 1 FROM app.exam_attempts WHERE id=a.id AND status='in_progress' FOR UPDATE;
        IF NOT FOUND THEN CONTINUE; END IF;
        INSERT INTO app.answers(id,course_id,user_id,exam_id,attempt_id,question_id)
          SELECT gen_random_uuid(),q.course_id,q.user_id,q.exam_id,a.id,q.id FROM app.questions q
          WHERE q.exam_id=a.exam_id ON CONFLICT(attempt_id,question_id) DO NOTHING;
        UPDATE app.exam_attempts SET status='submitted',submitted_at=clock_timestamp() WHERE id=a.id;
        INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key)
          VALUES(gen_random_uuid(),a.course_id,a.user_id,'grade_attempt',a.id,a.id::text)
          ON CONFLICT(kind,idempotency_key) DO NOTHING;
      END LOOP;
      DELETE FROM app.auth_sessions WHERE expires_at<clock_timestamp()-interval '24 hours';
      DELETE FROM app.account_tokens WHERE coalesce(consumed_at,expires_at)<clock_timestamp()-interval '24 hours';
      DELETE FROM app.idempotency_requests WHERE expires_at<clock_timestamp();
      DELETE FROM app.jobs WHERE state IN ('succeeded','failed','cancelled') AND updated_at<clock_timestamp()-interval '7 days';
    END $$;
    ALTER FUNCTION auth.scheduler_tick() OWNER TO tutoria_security;
    REVOKE ALL ON FUNCTION auth.scheduler_tick() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION auth.scheduler_tick() TO tutoria_worker;
    CREATE FUNCTION app.exam_frozen() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE eid uuid;s text;
    BEGIN
      IF TG_OP='DELETE' THEN eid=OLD.exam_id; ELSE eid=NEW.exam_id; END IF;
      SELECT status INTO s FROM app.exams WHERE id=eid;
      IF s='ready' THEN RAISE EXCEPTION 'EXAM_IMMUTABLE' USING ERRCODE='23514'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    """)
    for table in ("questions", "question_options", "question_keys", "correct_options"):
        bind.exec_driver_sql(
            f"CREATE TRIGGER immutable BEFORE INSERT OR UPDATE OR DELETE ON app.{table} FOR EACH ROW EXECUTE FUNCTION app.exam_frozen()"
        )
        bind.exec_driver_sql(f"REVOKE INSERT,UPDATE,DELETE ON app.{table} FROM tutoria_api")
    bind.exec_driver_sql("""
      REVOKE INSERT,UPDATE,DELETE ON app.grades,app.mastery_events,app.concept_mastery FROM tutoria_api;
      ALTER TABLE app.audit_events ENABLE ROW LEVEL SECURITY;
      ALTER TABLE app.audit_events FORCE ROW LEVEL SECURITY;
      CREATE POLICY audit_own ON app.audit_events FOR SELECT
        USING(actor_id=auth.uid() OR auth.has_course_role(course_id,ARRAY['coordinator']));
      CREATE POLICY audit_insert ON app.audit_events FOR INSERT WITH CHECK(actor_id=auth.uid());
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
