GRANT UPDATE,DELETE ON app.audit_events,app.policy_acceptances TO tutoria_security;

CREATE FUNCTION auth.purge_private_data(_cid uuid,_uid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t text;
BEGIN
 IF _cid IS NULL AND _uid IS NULL THEN RAISE EXCEPTION 'PURGE_SUBJECT_REQUIRED'; END IF;
 UPDATE app.exams SET status=CASE WHEN status IN ('generating','ready') THEN 'invalidated' ELSE status END,invalidated_at=clock_timestamp()
 WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid);
 IF EXISTS(SELECT FROM pg_attribute WHERE attrelid='app.exams'::regclass AND attname='evidence_attempt_id' AND NOT attisdropped) THEN
   EXECUTE 'UPDATE app.exams SET evidence_attempt_id=NULL WHERE ($1 IS NULL OR course_id=$1) AND ($2 IS NULL OR user_id=$2)' USING _cid,_uid;
 END IF;
 UPDATE app.messages SET in_reply_to=NULL,generation_id=NULL
 WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid);
 UPDATE app.grades SET supersedes_id=NULL
 WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid);
 UPDATE app.concept_sources SET generation_id=NULL WHERE generation_id IN
 (SELECT id FROM app.generation_runs WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid));
 UPDATE app.edge_sources SET generation_id=NULL WHERE generation_id IN
 (SELECT id FROM app.generation_runs WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid));
 DELETE FROM app.citations WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid);
 DELETE FROM app.generation_sources WHERE generation_id IN
 (SELECT id FROM app.generation_runs WHERE (_cid IS NULL OR course_id=_cid) AND (_uid IS NULL OR user_id=_uid));
 FOREACH t IN ARRAY ARRAY['mastery_events','grades','answer_selections','answers','correct_options',
 'question_keys','question_options','question_sources','exam_attempts','questions','exams','generated_artifacts',
 'messages','conversations','generation_runs','roadmap_steps','roadmaps','goal_concepts','learning_goals',
 'concept_mastery','learning_profiles','study_intervals','study_sessions','course_preferences'] LOOP
   EXECUTE format('DELETE FROM app.%I WHERE ($1 IS NULL OR course_id=$1) AND ($2 IS NULL OR user_id=$2)',t)
   USING _cid,_uid;
 END LOOP;
 DELETE FROM app.idempotency_requests WHERE (_uid IS NOT NULL AND user_id=_uid)
 OR (_cid IS NOT NULL AND position(_cid::text IN scope)>0);
END $$;

CREATE FUNCTION auth.request_scope_delete(_scope text,_subject uuid,_rid uuid,_confirmation text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE _course app.courses;_user app.users;existing uuid;
BEGIN
 IF _scope NOT IN ('course','account') THEN RAISE EXCEPTION 'INVALID_SCOPE' USING ERRCODE='22023'; END IF;
 IF _scope='course' THEN
   IF NOT auth.has_course_role(_subject,ARRAY['coordinator']) THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF;
   SELECT * INTO _course FROM app.courses WHERE id=_subject FOR UPDATE;
   IF _course.name IS DISTINCT FROM _confirmation THEN RAISE EXCEPTION 'CONFIRMATION_REQUIRED' USING ERRCODE='23514'; END IF;
 ELSE
   IF _subject IS DISTINCT FROM auth.uid() THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF;
   PERFORM c.id FROM app.courses c JOIN app.memberships m ON m.course_id=c.id
   WHERE m.user_id=_subject AND c.status IN ('active','archived') ORDER BY c.id FOR UPDATE OF c;
   SELECT * INTO _user FROM app.users WHERE id=_subject AND status IN ('active','pending') FOR UPDATE;
   IF _user.id IS NULL THEN RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
   IF _user.nickname IS DISTINCT FROM _confirmation THEN RAISE EXCEPTION 'CONFIRMATION_REQUIRED' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT FROM app.memberships m JOIN app.courses c ON c.id=m.course_id
      WHERE m.user_id=_subject AND m.role='coordinator' AND m.status='active' AND c.status IN ('active','archived')
      AND NOT EXISTS(SELECT FROM app.memberships other WHERE other.course_id=m.course_id
        AND other.user_id<>_subject AND other.role='coordinator' AND other.status='active')) THEN
     RAISE EXCEPTION 'LAST_COORDINATOR' USING ERRCODE='23514';
   END IF;
 END IF;
 SELECT id INTO existing FROM app.deletion_requests WHERE scope=_scope AND subject_id=_subject
 AND state IN ('queued','running') ORDER BY requested_at DESC LIMIT 1;
 IF existing IS NOT NULL THEN RETURN existing; END IF;
 IF _scope='course' THEN
   UPDATE app.courses SET status='deletion_pending',corpus_revision=corpus_revision+1 WHERE id=_subject;
   UPDATE app.documents SET status='deletion_pending',active_run_id=NULL WHERE course_id=_subject;
   UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,payload='{}'
      WHERE course_id=_subject AND state IN ('queued','running');
 ELSE
   UPDATE app.users SET status='deletion_pending' WHERE id=_subject;
   UPDATE app.auth_sessions SET revoked_at=clock_timestamp() WHERE user_id=_subject;
   UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,payload='{}'
      WHERE requested_by=_subject AND state IN ('queued','running');
 END IF;
 INSERT INTO app.deletion_requests(id,scope,subject_id,requested_by,deadline_at)
 VALUES(_rid,_scope,_subject,auth.uid(),clock_timestamp()+interval '1 hour');
 INSERT INTO app.deletion_ledger(id,scope,subject_id,requested_at,expires_at)
 VALUES(_rid,_scope,_subject,clock_timestamp(),clock_timestamp()+interval '30 days');
 INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key)
 VALUES(gen_random_uuid(),CASE WHEN _scope='course' THEN _subject ELSE NULL END,auth.uid(),'purge',_rid,_rid::text);
 INSERT INTO app.audit_events(id,course_id,actor_id,action,entity_type,entity_id,request_id,result)
 VALUES(gen_random_uuid(),CASE WHEN _scope='course' THEN _subject ELSE NULL END,auth.uid(),_scope||'.withdraw',_scope,_subject,_rid,'success');
 RETURN _rid;
END $$;

CREATE FUNCTION auth.purge_scope(_rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.deletion_requests;did uuid;child uuid;t text;
BEGIN
 SELECT * INTO r FROM app.deletion_requests WHERE id=_rid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'INVALID_DELETION_REQUEST'; END IF;
 IF r.scope='document' THEN RETURN auth.purge_document(_rid); END IF;
 IF r.scope='course' THEN
   PERFORM 1 FROM app.courses WHERE id=r.subject_id FOR UPDATE;
   UPDATE app.deletion_requests SET state='running' WHERE id=_rid;
   FOR did IN SELECT id FROM app.documents WHERE course_id=r.subject_id ORDER BY id LOOP
     child=gen_random_uuid();
     INSERT INTO app.deletion_requests(id,scope,subject_id,deadline_at)
     VALUES(child,'document',did,clock_timestamp()+interval '1 hour');
     PERFORM auth.purge_document(child);
     DELETE FROM app.deletion_requests WHERE id=child;
   END LOOP;
   PERFORM auth.purge_private_data(r.subject_id,NULL);
   FOREACH t IN ARRAY ARRAY['edge_sources','concept_sources','concept_aliases','concept_edges','concepts',
      'course_embedding_config','course_invites','memberships'] LOOP
     EXECUTE format('DELETE FROM app.%I WHERE course_id=$1',t) USING r.subject_id;
   END LOOP;
   UPDATE app.jobs SET course_id=NULL,requested_by=NULL,payload='{}',
     state=CASE WHEN state IN ('queued','running') AND NOT(kind='purge' AND target_id=r.id) THEN 'cancelled' ELSE state END,
     lease_token=CASE WHEN kind='purge' AND target_id=r.id THEN lease_token ELSE NULL END,
     lease_until=CASE WHEN kind='purge' AND target_id=r.id THEN lease_until ELSE NULL END
     WHERE course_id=r.subject_id;
   DELETE FROM app.courses WHERE id=r.subject_id;
 ELSIF r.scope='account' THEN
   PERFORM c.id FROM app.courses c JOIN app.memberships m ON m.course_id=c.id
   WHERE m.user_id=r.subject_id AND c.status IN ('active','archived') ORDER BY c.id FOR UPDATE OF c;
   IF EXISTS(SELECT FROM app.memberships m JOIN app.courses c ON c.id=m.course_id
      WHERE m.user_id=r.subject_id AND m.role='coordinator' AND m.status='active' AND c.status IN ('active','archived')
      AND NOT EXISTS(SELECT FROM app.memberships x WHERE x.course_id=m.course_id AND x.user_id<>r.subject_id AND x.role='coordinator' AND x.status='active'))
   THEN RAISE EXCEPTION 'LAST_COORDINATOR' USING ERRCODE='23514'; END IF;
   PERFORM 1 FROM app.users WHERE id=r.subject_id FOR UPDATE;
   UPDATE app.deletion_requests SET state='running' WHERE id=_rid;
   PERFORM auth.purge_private_data(NULL,r.subject_id);
   UPDATE app.documents SET uploaded_by=NULL WHERE uploaded_by=r.subject_id;
   UPDATE app.document_versions SET uploaded_by=NULL WHERE uploaded_by=r.subject_id;
   UPDATE app.courses SET created_by=NULL WHERE created_by=r.subject_id;
   UPDATE app.concepts SET created_by=NULL WHERE created_by=r.subject_id;
   UPDATE app.course_invites SET created_by=NULL WHERE created_by=r.subject_id;
   UPDATE app.audit_events SET actor_id=NULL WHERE actor_id=r.subject_id;
   UPDATE app.jobs SET requested_by=NULL,payload='{}',
     state=CASE WHEN state IN ('queued','running') AND NOT(kind='purge' AND target_id=r.id) THEN 'cancelled' ELSE state END,
     lease_token=CASE WHEN kind='purge' AND target_id=r.id THEN lease_token ELSE NULL END,
     lease_until=CASE WHEN kind='purge' AND target_id=r.id THEN lease_until ELSE NULL END
     WHERE requested_by=r.subject_id;
   UPDATE app.deletion_requests SET requested_by=NULL WHERE requested_by=r.subject_id;
   DELETE FROM app.memberships WHERE user_id=r.subject_id;
   DELETE FROM app.auth_sessions WHERE user_id=r.subject_id;
   IF to_regclass('app.account_email_changes') IS NOT NULL THEN
     EXECUTE 'DELETE FROM app.account_email_changes WHERE user_id=$1' USING r.subject_id;
   END IF;
   DELETE FROM app.account_tokens WHERE user_id=r.subject_id;
   DELETE FROM app.policy_acceptances WHERE user_id=r.subject_id;
   DELETE FROM app.users WHERE id=r.subject_id;
 ELSE
   RAISE EXCEPTION 'INVALID_DELETION_SCOPE';
 END IF;
 RETURN jsonb_build_object('id',r.id,'scope',r.scope,'subjectId',r.subject_id,'requestedAt',r.requested_at);
END $$;

CREATE FUNCTION auth.heartbeat_purge_job(_jid uuid,_lease uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH changed AS (
   UPDATE app.jobs SET lease_until=clock_timestamp()+interval '120 seconds'
   WHERE id=_jid AND kind='purge' AND state='running' AND lease_token=_lease
   AND lease_until>clock_timestamp() RETURNING id
 ) SELECT EXISTS(SELECT FROM changed)
$$;

CREATE FUNCTION auth.finish_purge_job(_jid uuid,_lease uuid,_rid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM 1 FROM app.jobs WHERE id=_jid AND kind='purge' AND target_id=_rid AND state='running'
   AND lease_token=_lease AND lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'LEASE_LOST' USING ERRCODE='23514'; END IF;
 PERFORM auth.confirm_purge(_rid);
 UPDATE app.jobs SET state='succeeded',lease_token=NULL,lease_until=NULL,payload='{}' WHERE id=_jid;
END $$;

ALTER FUNCTION auth.purge_private_data(uuid,uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.request_scope_delete(text,uuid,uuid,text) OWNER TO tutoria_security;
ALTER FUNCTION auth.purge_scope(uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.finish_purge_job(uuid,uuid,uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.heartbeat_purge_job(uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.purge_private_data(uuid,uuid),auth.request_scope_delete(text,uuid,uuid,text),
 auth.purge_scope(uuid),auth.finish_purge_job(uuid,uuid,uuid),auth.heartbeat_purge_job(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.request_scope_delete(text,uuid,uuid,text) TO tutoria_api;
GRANT EXECUTE ON FUNCTION auth.purge_scope(uuid),auth.finish_purge_job(uuid,uuid,uuid),auth.heartbeat_purge_job(uuid,uuid) TO tutoria_worker;
