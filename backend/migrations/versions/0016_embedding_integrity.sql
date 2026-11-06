CREATE FUNCTION app.embedding_space_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (to_jsonb(NEW)-'enabled') IS DISTINCT FROM (to_jsonb(OLD)-'enabled') THEN
  RAISE EXCEPTION 'EMBEDDING_SPACE_IMMUTABLE' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable BEFORE UPDATE ON app.embedding_spaces FOR EACH ROW EXECUTE FUNCTION app.embedding_space_immutable();

CREATE FUNCTION auth.cancel_embedding_migration(cid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target uuid;
BEGIN
 IF NOT auth.has_course_role(cid,ARRAY['coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
 SELECT pending_space_id INTO target FROM app.course_embedding_config WHERE course_id=cid FOR UPDATE;
 IF target IS NULL THEN RETURN; END IF;
 UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,error_code='MIGRATION_CANCELLED'
 WHERE course_id=cid AND state IN ('queued','running') AND kind='ingest_document'
 AND target_id IN(SELECT id FROM app.processing_runs WHERE course_id=cid AND embedding_space_id=target);
 UPDATE app.processing_runs SET state='cancelled',finished_at=clock_timestamp(),error_code='MIGRATION_CANCELLED'
 WHERE course_id=cid AND embedding_space_id=target AND state IN ('queued','running');
 UPDATE app.course_embedding_config SET pending_space_id=NULL WHERE course_id=cid;
END $$;
ALTER FUNCTION auth.cancel_embedding_migration(uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.cancel_embedding_migration(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.cancel_embedding_migration(uuid) TO tutoria_api;

-- Purge failure handling remains possible after the requester account is removed.
CREATE FUNCTION auth.fail_purge_job(jid uuid,lease uuid,code text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.jobs;
BEGIN
 SELECT * INTO j FROM app.jobs WHERE id=jid AND kind='purge' AND state='running'
  AND lease_token=lease AND lease_until>clock_timestamp() FOR UPDATE;
 IF j.id IS NULL THEN RETURN false; END IF;
 UPDATE app.jobs SET state=CASE WHEN attempts<max_attempts THEN 'queued' ELSE 'failed' END,
  error_code=left(code,80),lease_token=NULL,lease_until=NULL,locked_by=NULL,
  run_after=clock_timestamp()+make_interval(secs=>CASE WHEN attempts=1 THEN 10 ELSE 30 END)
  WHERE id=jid;
 UPDATE app.deletion_requests SET state=CASE WHEN j.attempts<j.max_attempts THEN 'running' ELSE 'failed' END,
  error_code=left(code,80) WHERE id=j.target_id;
 RETURN true;
END $$;
ALTER FUNCTION auth.fail_purge_job(uuid,uuid,text) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.fail_purge_job(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.fail_purge_job(uuid,uuid,text) TO tutoria_worker;
