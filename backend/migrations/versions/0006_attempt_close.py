"""Atomic server-side submission, including deadline expiry."""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql("""
    GRANT SELECT,INSERT,UPDATE ON app.answers,app.exam_attempts,app.jobs TO tutoria_security;
    GRANT SELECT ON app.questions TO tutoria_security;
    CREATE OR REPLACE FUNCTION app.answer_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE a app.exam_attempts;aid uuid;
    BEGIN
      IF TG_TABLE_NAME='answer_selections' THEN SELECT attempt_id INTO aid FROM app.answers WHERE id=NEW.answer_id;
      ELSE aid=NEW.attempt_id;
        IF current_user='tutoria_security' AND TG_OP='INSERT' AND NEW.text_answer IS NULL THEN RETURN NEW; END IF;
      END IF;
      SELECT * INTO a FROM app.exam_attempts WHERE id=aid FOR UPDATE;
      IF a.status<>'in_progress' OR a.deadline_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'ATTEMPT_CLOSED' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION auth.submit_attempt(aid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE a app.exam_attempts;cid uuid;
    BEGIN
      SELECT course_id INTO cid FROM app.exam_attempts WHERE id=aid AND user_id=auth.uid();
      IF cid IS NULL OR NOT auth.has_course_role(cid,ARRAY['student','coordinator']) THEN
        RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
      PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
      SELECT * INTO a FROM app.exam_attempts WHERE id=aid AND user_id=auth.uid() FOR UPDATE;
      IF a.status<>'in_progress' THEN RETURN a.id; END IF;
      INSERT INTO app.answers(id,course_id,user_id,exam_id,attempt_id,question_id)
      SELECT gen_random_uuid(),q.course_id,q.user_id,q.exam_id,a.id,q.id FROM app.questions q
      WHERE q.exam_id=a.exam_id ON CONFLICT(attempt_id,question_id) DO NOTHING;
      UPDATE app.exam_attempts SET status='submitted',submitted_at=clock_timestamp() WHERE id=a.id;
      INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key)
      VALUES(gen_random_uuid(),a.course_id,a.user_id,'grade_attempt',a.id,a.id::text)
      ON CONFLICT(kind,idempotency_key) DO NOTHING;
      RETURN a.id;
    END $$;
    ALTER FUNCTION auth.submit_attempt(uuid) OWNER TO tutoria_security;
    REVOKE ALL ON FUNCTION auth.submit_attempt(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION auth.submit_attempt(uuid) TO tutoria_api,tutoria_worker;
    GRANT UPDATE ON app.courses TO tutoria_security;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
