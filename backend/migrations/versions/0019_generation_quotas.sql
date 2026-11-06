CREATE TABLE auth.generation_limits (
 version text PRIMARY KEY,effective_from timestamptz NOT NULL,
 max_active integer NOT NULL CHECK(max_active>0)
);
INSERT INTO auth.generation_limits VALUES('generation_limits_v1','2026-01-01T00:00:00Z',2);
ALTER TABLE auth.generation_limits OWNER TO tutoria_security;
REVOKE ALL ON auth.generation_limits FROM PUBLIC,tutoria_api,tutoria_worker;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON auth.generation_limits
 FOR EACH ROW EXECUTE FUNCTION app.immutable_upload_limits();

CREATE FUNCTION auth.reserve_generation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE maximum integer;active_count bigint;
BEGIN
 -- Administrative restores/backfills may insert historical records. Application
 -- connections are separately forbidden from using a superuser role.
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper) THEN RETURN NEW; END IF;
 IF NEW.user_id IS DISTINCT FROM auth.uid()
 OR NOT auth.has_course_role(NEW.course_id,ARRAY['student','coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501';
 END IF;
 IF NEW.status NOT IN ('queued','running') THEN RETURN NEW; END IF;
 PERFORM 1 FROM app.courses WHERE id=NEW.course_id AND status='active' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('tutoria.generation.user.'||NEW.user_id::text,0));
 SELECT max_active INTO STRICT maximum FROM auth.generation_limits
 WHERE effective_from<=clock_timestamp() ORDER BY effective_from DESC,version DESC LIMIT 1;
 -- Count all courses, including generations hidden by membership withdrawal.
 SELECT count(*) INTO active_count FROM app.generation_runs
 WHERE user_id=NEW.user_id AND status IN ('queued','running');
 IF active_count>=maximum THEN
  RAISE EXCEPTION 'GENERATION_QUOTA_EXCEEDED' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION auth.reserve_generation() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.reserve_generation() FROM PUBLIC;
CREATE TRIGGER reserve_generation BEFORE INSERT ON app.generation_runs
 FOR EACH ROW EXECUTE FUNCTION auth.reserve_generation();

CREATE OR REPLACE FUNCTION auth.claim_job(worker text) RETURNS SETOF app.jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.jobs;
BEGIN
 SELECT * INTO j FROM app.jobs WHERE state='queued' AND run_after<=clock_timestamp()
 AND attempts<max_attempts AND (deadline_at IS NULL OR deadline_at>clock_timestamp())
 ORDER BY (kind='purge') DESC,run_after,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE app.jobs SET state='running',attempts=attempts+1,lease_token=gen_random_uuid(),locked_by=worker,
 lease_until=clock_timestamp()+interval '120 seconds' WHERE id=j.id RETURNING * INTO j;
 RETURN NEXT j;
END $$;

-- Recover the job and its public generation together so a crashed worker
-- cannot retain capacity forever after its final lease/deadline expires.
CREATE OR REPLACE FUNCTION auth.recover_jobs() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE app.jobs SET
  state=CASE WHEN attempts>=max_attempts OR deadline_at<=clock_timestamp() THEN 'failed' ELSE 'queued' END,
  lease_token=NULL,locked_by=NULL,lease_until=NULL,
  error_code=CASE WHEN deadline_at<=clock_timestamp() THEN 'DEADLINE_EXCEEDED'
   WHEN attempts>=max_attempts THEN 'RETRY_LIMIT_EXCEEDED' ELSE 'LEASE_EXPIRED' END,
  run_after=clock_timestamp()+make_interval(secs=>(CASE WHEN attempts<=1 THEN 10 ELSE 30 END)*(0.8+random()*0.4))
 WHERE (state='running' AND lease_until<=clock_timestamp())
 OR (state IN ('queued','running') AND deadline_at<=clock_timestamp())
 OR (state='queued' AND attempts>=max_attempts);

 WITH terminal AS (
  SELECT g.id,bool_or(j.state='failed') failed FROM app.generation_runs g
  JOIN app.jobs j ON j.course_id=g.course_id AND
   ((j.kind='generate_chat' AND j.target_id=g.id) OR (j.kind='generate_exam' AND EXISTS(
    SELECT 1 FROM app.exams e WHERE e.course_id=g.course_id AND e.generation_id=g.id AND e.id=j.target_id)))
  WHERE g.status IN ('queued','running') AND j.state IN ('failed','cancelled') GROUP BY g.id
 )
 UPDATE app.generation_runs g SET status=CASE WHEN t.failed THEN 'failed' ELSE 'cancelled' END,
 error_code=CASE WHEN t.failed THEN 'JOB_FAILED' ELSE 'JOB_CANCELLED' END,finished_at=clock_timestamp()
 FROM terminal t WHERE g.id=t.id;
 UPDATE app.messages m SET status=g.status,parts='[]'
 FROM app.generation_runs g WHERE g.course_id=m.course_id AND g.id=m.generation_id
 AND g.status IN ('failed','cancelled') AND m.status IN ('pending','streaming');
 UPDATE app.exams e SET status=CASE WHEN g.status='failed' THEN 'failed' ELSE 'invalidated' END,
 invalidated_at=CASE WHEN g.status='cancelled' THEN clock_timestamp() ELSE e.invalidated_at END
 FROM app.generation_runs g WHERE g.course_id=e.course_id AND g.id=e.generation_id
 AND g.status IN ('failed','cancelled') AND e.status='generating';
END $$;
