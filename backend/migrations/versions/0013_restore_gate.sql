DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='tutoria_backup') THEN
   CREATE ROLE tutoria_backup NOLOGIN NOSUPERUSER BYPASSRLS;
 END IF;
END $$;
GRANT pg_read_all_data TO tutoria_backup;

-- An isolated restore creates this gate BEFORE importing the archive. Production
-- databases with no restore_control schema are not in a restore operation.
CREATE FUNCTION auth.restore_is_ready() RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ready boolean;
BEGIN
 IF to_regclass('restore_control.gate') IS NULL THEN RETURN true; END IF;
 EXECUTE 'SELECT state=''verified'' FROM restore_control.gate WHERE singleton' INTO ready;
 RETURN coalesce(ready,false);
END $$;
ALTER FUNCTION auth.restore_is_ready() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.restore_is_ready() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.restore_is_ready() TO tutoria_api,tutoria_worker;

CREATE FUNCTION auth.replay_deletion(_rid uuid,_scope text,_subject uuid,_requested timestamptz) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE existing app.deletion_requests;
BEGIN
 IF _scope NOT IN ('document','course','account') OR _requested IS NULL THEN RAISE EXCEPTION 'INVALID_LEDGER_RECORD'; END IF;
 SELECT * INTO existing FROM app.deletion_requests WHERE id=_rid;
 IF existing.id IS NOT NULL AND (existing.scope<>_scope OR existing.subject_id<>_subject) THEN
   RAISE EXCEPTION 'LEDGER_ID_CONFLICT'; END IF;
 INSERT INTO app.deletion_requests(id,scope,subject_id,requested_at,deadline_at)
 VALUES(_rid,_scope,_subject,_requested,clock_timestamp()+interval '1 hour') ON CONFLICT(id) DO NOTHING;
 INSERT INTO app.deletion_ledger(id,scope,subject_id,requested_at,expires_at)
 VALUES(_rid,_scope,_subject,_requested,_requested+interval '30 days') ON CONFLICT(id) DO NOTHING;
 IF _scope='document' THEN
   UPDATE app.documents SET status='deletion_pending',active_run_id=NULL WHERE id=_subject;
 ELSIF _scope='course' THEN
   UPDATE app.courses SET status='deletion_pending' WHERE id=_subject;
   UPDATE app.documents SET status='deletion_pending',active_run_id=NULL WHERE course_id=_subject;
 ELSE
   UPDATE app.users SET status='deletion_pending' WHERE id=_subject;
   UPDATE app.auth_sessions SET revoked_at=clock_timestamp() WHERE user_id=_subject;
 END IF;
 PERFORM auth.purge_scope(_rid);
 -- The record was read from the separately persisted, authenticated ledger.
 PERFORM auth.confirm_purge(_rid);
END $$;
ALTER FUNCTION auth.replay_deletion(uuid,text,uuid,timestamptz) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.replay_deletion(uuid,text,uuid,timestamptz) FROM PUBLIC;
-- No application or worker EXECUTE grant: only an administrative restore session
-- can replay external records. BYPASSRLS alone does not grant function execution.
