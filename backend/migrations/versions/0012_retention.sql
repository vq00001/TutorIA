CREATE FUNCTION auth.redact_lineage(_cid uuid,_parents uuid[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE _exams uuid[];_generations uuid[];_affected_users uuid[];
BEGIN
 SELECT array_agg(DISTINCT q.exam_id) INTO _exams FROM app.questions q
 JOIN app.question_sources s ON s.question_id=q.id WHERE s.parent_id=ANY(_parents);
 SELECT array_agg(DISTINCT gid) INTO _generations FROM (
   SELECT generation_id gid FROM app.generation_sources WHERE parent_id=ANY(_parents)
   UNION SELECT generation_id FROM app.exams WHERE id=ANY(_exams)
   UNION SELECT generation_id FROM app.concept_sources WHERE parent_id=ANY(_parents)
   UNION SELECT generation_id FROM app.edge_sources WHERE parent_id=ANY(_parents)
   UNION SELECT g.generation_id FROM app.grades g JOIN app.answers a ON a.id=g.answer_id WHERE a.exam_id=ANY(_exams)
 ) g WHERE gid IS NOT NULL;
 SELECT array_agg(DISTINCT e.user_id) INTO _affected_users FROM app.mastery_events e
 JOIN app.grades g ON g.id=e.grade_id JOIN app.answers a ON a.id=g.answer_id WHERE a.exam_id=ANY(_exams);
 UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,payload='{}'
 WHERE course_id=_cid AND state IN ('queued','running') AND
 (target_id=ANY(_generations) OR target_id=ANY(_exams)
 OR target_id IN(SELECT id FROM app.exam_attempts WHERE exam_id=ANY(_exams)));
 UPDATE app.messages SET parts='[]',status='redacted',redacted_at=clock_timestamp() WHERE generation_id=ANY(_generations);
 UPDATE app.generated_artifacts SET content='{}',title='Contenido retirado',status='redacted' WHERE generation_id=ANY(_generations);
 UPDATE app.citations SET parent_id=NULL,label='Fuente retirada',locator='{}',status='source_removed' WHERE parent_id=ANY(_parents);
 DELETE FROM app.generation_sources WHERE parent_id=ANY(_parents);
 UPDATE app.generation_runs SET status=CASE WHEN status IN ('queued','running') THEN 'cancelled'
   WHEN status='completed' THEN 'invalidated' ELSE status END,parameters='{}' WHERE id=ANY(_generations);
 UPDATE app.exams SET status=CASE WHEN status IN ('generating','ready') THEN 'invalidated' ELSE status END,title='Evaluación retirada',invalidated_at=clock_timestamp() WHERE id=ANY(_exams);
 UPDATE app.exam_attempts SET status='invalidated',score_percent=NULL WHERE exam_id=ANY(_exams);
 DELETE FROM app.mastery_events WHERE grade_id IN(SELECT g.id FROM app.grades g JOIN app.answers a ON a.id=g.answer_id WHERE a.exam_id=ANY(_exams));
 UPDATE app.grades SET supersedes_id=NULL WHERE answer_id IN(SELECT id FROM app.answers WHERE exam_id=ANY(_exams));
 DELETE FROM app.grades WHERE answer_id IN(SELECT id FROM app.answers WHERE exam_id=ANY(_exams));
 DELETE FROM app.answer_selections WHERE exam_id=ANY(_exams);
 DELETE FROM app.answers WHERE exam_id=ANY(_exams);
 DELETE FROM app.correct_options WHERE exam_id=ANY(_exams);
 DELETE FROM app.question_keys WHERE exam_id=ANY(_exams);
 DELETE FROM app.question_options WHERE exam_id=ANY(_exams);
 DELETE FROM app.question_sources WHERE question_id IN(SELECT id FROM app.questions WHERE exam_id=ANY(_exams));
 UPDATE app.questions SET prompt='Pregunta retirada por eliminación de fuente' WHERE exam_id=ANY(_exams);
 DELETE FROM app.concept_sources WHERE parent_id=ANY(_parents);
 DELETE FROM app.edge_sources WHERE parent_id=ANY(_parents);
 UPDATE app.concepts c SET description=CASE WHEN description_origin='llm' THEN '' ELSE description END,
  label=CASE WHEN label_origin='llm' THEN 'Concepto retirado' ELSE label END,
  status=CASE WHEN origin='llm' THEN 'retired' ELSE status END
 WHERE course_id=_cid AND NOT EXISTS(SELECT FROM app.concept_sources s WHERE s.concept_id=c.id)
 AND (origin='llm' OR description_origin='llm' OR label_origin='llm');
 UPDATE app.concept_edges e SET status='retired' WHERE course_id=_cid AND
  (origin='llm' AND NOT EXISTS(SELECT FROM app.edge_sources s WHERE s.edge_id=e.id)
   OR source_id IN(SELECT id FROM app.concepts WHERE course_id=_cid AND status='retired')
   OR target_id IN(SELECT id FROM app.concepts WHERE course_id=_cid AND status='retired'));
 UPDATE app.roadmaps SET status='stale' WHERE course_id=_cid AND status='active';
 UPDATE app.roadmap_steps SET reason='Fuente retirada' WHERE course_id=_cid;
 DELETE FROM app.concept_mastery WHERE course_id=_cid AND user_id=ANY(_affected_users);
 INSERT INTO app.concept_mastery(course_id,user_id,concept_id,alpha,beta,evidence_count,level)
 SELECT course_id,user_id,concept_id,1+sum(weight*value),1+sum(weight*(1-value)),count(*),
 CASE WHEN (1+sum(weight*value))/(2+sum(weight))>=.8 AND count(*)>=3 THEN 'mastered'
 WHEN (1+sum(weight*value))/(2+sum(weight))<.6 THEN 'reinforce' ELSE 'developing' END
 FROM app.mastery_events WHERE course_id=_cid AND user_id=ANY(_affected_users) AND valid GROUP BY course_id,user_id,concept_id;
 UPDATE app.learning_profiles SET mastery_revision=mastery_revision+1 WHERE course_id=_cid AND user_id=ANY(_affected_users);
END $$;

CREATE OR REPLACE FUNCTION auth.purge_document(rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.deletion_requests;_cid uuid;_parents uuid[];
BEGIN
 SELECT * INTO r FROM app.deletion_requests WHERE id=rid AND scope='document';
 IF r.id IS NULL THEN RAISE EXCEPTION 'INVALID_DELETION_REQUEST'; END IF;
 SELECT course_id INTO _cid FROM app.documents WHERE id=r.subject_id;
 IF _cid IS NOT NULL THEN
  PERFORM 1 FROM app.courses WHERE id=_cid FOR UPDATE;
  PERFORM 1 FROM app.documents WHERE id=r.subject_id FOR UPDATE;
  UPDATE app.deletion_requests SET state='running' WHERE id=rid;
  SELECT array_agg(id) INTO _parents FROM app.parent_chunks WHERE document_id=r.subject_id;
  PERFORM auth.redact_lineage(_cid,_parents);
  UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,payload='{}'
  WHERE target_id IN(SELECT id FROM app.processing_runs WHERE document_id=r.subject_id) AND state IN ('queued','running');
  DELETE FROM app.idempotency_requests WHERE position(r.subject_id::text IN response_body::text)>0;
  UPDATE app.documents SET active_run_id=NULL WHERE id=r.subject_id;
  DELETE FROM app.chunk_embeddings WHERE run_id IN(SELECT id FROM app.processing_runs WHERE document_id=r.subject_id);
  DELETE FROM app.child_chunks WHERE document_id=r.subject_id;
  DELETE FROM app.parent_chunks WHERE document_id=r.subject_id;
  DELETE FROM app.processing_runs WHERE document_id=r.subject_id;
  DELETE FROM app.file_contents WHERE document_id=r.subject_id;
  DELETE FROM app.document_versions WHERE document_id=r.subject_id;
  DELETE FROM app.documents WHERE id=r.subject_id;
  UPDATE app.courses SET corpus_revision=corpus_revision+1 WHERE id=_cid;
 END IF;
 RETURN jsonb_build_object('id',r.id,'scope','document','subjectId',r.subject_id,'requestedAt',r.requested_at);
END $$;

CREATE FUNCTION auth.purge_historical_run(_run uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.processing_runs;_parents uuid[];
BEGIN
 SELECT * INTO r FROM app.processing_runs WHERE id=_run;
 IF r.id IS NULL THEN RETURN; END IF;
 PERFORM 1 FROM app.courses WHERE id=r.course_id FOR UPDATE;
 PERFORM 1 FROM app.documents WHERE id=r.document_id FOR UPDATE;
 IF EXISTS(SELECT FROM app.documents WHERE active_run_id=_run) THEN RAISE EXCEPTION 'ACTIVE_RUN_RETAINED'; END IF;
 UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,payload='{}'
 WHERE target_id=_run AND state IN ('queued','running');
 SELECT array_agg(id) INTO _parents FROM app.parent_chunks WHERE run_id=_run;
 PERFORM auth.redact_lineage(r.course_id,_parents);
 DELETE FROM app.chunk_embeddings WHERE run_id=_run;
 DELETE FROM app.child_chunks WHERE run_id=_run;
 DELETE FROM app.parent_chunks WHERE run_id=_run;
 DELETE FROM app.processing_runs WHERE id=_run;
END $$;

CREATE FUNCTION auth.retention_cleanup(_at timestamptz DEFAULT clock_timestamp()) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
 -- Retained originals are not aged out while supporting an active run.
 FOR r IN SELECT v.id,v.course_id,v.document_id FROM app.document_versions v
 WHERE v.superseded_at IS NOT NULL AND v.superseded_at<=_at-interval '30 days'
 AND NOT EXISTS(SELECT FROM app.documents d JOIN app.processing_runs p ON p.id=d.active_run_id WHERE p.version_id=v.id)
 ORDER BY v.course_id,v.document_id,v.id LOOP
  PERFORM 1 FROM app.courses WHERE id=r.course_id FOR UPDATE;
  PERFORM 1 FROM app.documents WHERE id=r.document_id FOR UPDATE;
  IF EXISTS(SELECT FROM app.documents d JOIN app.processing_runs p ON p.id=d.active_run_id WHERE p.version_id=r.id) THEN CONTINUE; END IF;
  PERFORM auth.purge_historical_run(p.id) FROM app.processing_runs p WHERE p.version_id=r.id ORDER BY p.id;
  DELETE FROM app.file_contents WHERE version_id=r.id;
  DELETE FROM app.document_versions WHERE id=r.id;
 END LOOP;
 FOR r IN SELECT p.id FROM app.processing_runs p WHERE p.finished_at IS NOT NULL
 AND NOT EXISTS(SELECT FROM app.documents d WHERE d.active_run_id=p.id)
 AND ((p.state IN ('failed','cancelled') AND p.finished_at<=_at-interval '24 hours')
 OR (p.state='ready' AND p.finished_at<=_at-interval '30 days')) ORDER BY p.course_id,p.document_id,p.id LOOP
  PERFORM auth.purge_historical_run(r.id);
 END LOOP;
 FOR r IN SELECT course_id,user_id FROM app.memberships WHERE status IN ('left','removed')
 AND left_at IS NOT NULL AND left_at<=_at-interval '30 days' ORDER BY course_id,user_id LOOP
  PERFORM 1 FROM app.courses WHERE id=r.course_id FOR UPDATE;
  PERFORM auth.purge_private_data(r.course_id,r.user_id);
 END LOOP;
 DELETE FROM app.auth_sessions WHERE expires_at<=_at-interval '24 hours';
 DELETE FROM app.account_tokens WHERE coalesce(consumed_at,expires_at)<=_at-interval '24 hours';
 DELETE FROM app.idempotency_requests WHERE expires_at<=_at;
 DELETE FROM app.jobs WHERE state IN ('succeeded','failed','cancelled') AND updated_at<=_at-interval '7 days';
 DELETE FROM app.audit_events WHERE created_at<=_at-interval '90 days';
 -- Never expire a ledger whose external persistence has not been acknowledged.
 DELETE FROM app.deletion_ledger WHERE expires_at<=_at AND completed_at IS NOT NULL;
 IF to_regprocedure('auth.cleanup_auth_limits()') IS NOT NULL THEN
   EXECUTE 'SELECT auth.cleanup_auth_limits()';
 END IF;
END $$;
ALTER FUNCTION auth.redact_lineage(uuid,uuid[]) OWNER TO tutoria_security;
ALTER FUNCTION auth.purge_document(uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.purge_historical_run(uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.retention_cleanup(timestamptz) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.redact_lineage(uuid,uuid[]),auth.purge_historical_run(uuid),auth.retention_cleanup(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.retention_cleanup(timestamptz) TO tutoria_worker;

CREATE OR REPLACE FUNCTION auth.request_document_delete(cid uuid,did uuid,rid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d app.documents;_parents uuid[];_gids uuid[];_exams uuid[];
BEGIN
 IF NOT auth.has_course_role(cid,ARRAY['student','coordinator']) THEN
   RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
 SELECT * INTO d FROM app.documents WHERE id=did AND course_id=cid FOR UPDATE;
 IF d.id IS NULL THEN RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE='42501'; END IF;
 IF d.uploaded_by IS DISTINCT FROM auth.uid() AND NOT auth.has_course_role(cid,ARRAY['coordinator']) THEN
   RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF d.status='deletion_pending' THEN
   RETURN (SELECT id FROM app.deletion_requests WHERE scope='document' AND subject_id=did ORDER BY requested_at DESC LIMIT 1);
 END IF;
 SELECT array_agg(id) INTO _parents FROM app.parent_chunks WHERE document_id=did;
 SELECT array_agg(DISTINCT q.exam_id) INTO _exams FROM app.questions q JOIN app.question_sources s ON s.question_id=q.id WHERE s.parent_id=ANY(_parents);
 SELECT array_agg(DISTINCT gid) INTO _gids FROM (
   SELECT generation_id gid FROM app.generation_sources WHERE parent_id=ANY(_parents)
   UNION SELECT generation_id FROM app.exams WHERE id=ANY(_exams)
   UNION SELECT generation_id FROM app.concept_sources WHERE parent_id=ANY(_parents)
   UNION SELECT generation_id FROM app.edge_sources WHERE parent_id=ANY(_parents)
 ) all_sources WHERE gid IS NOT NULL;
 UPDATE app.documents SET status='deletion_pending',active_run_id=NULL WHERE id=did;
 UPDATE app.courses SET corpus_revision=corpus_revision+1 WHERE id=cid;
 UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL,payload='{}'
 WHERE course_id=cid AND state IN ('queued','running') AND
 (target_id IN(SELECT id FROM app.processing_runs WHERE document_id=did)
 OR target_id=ANY(_gids) OR target_id=ANY(_exams)
 OR target_id IN(SELECT id FROM app.exam_attempts WHERE exam_id=ANY(_exams)));
 UPDATE app.messages SET parts='[]',status='redacted',redacted_at=clock_timestamp() WHERE generation_id=ANY(_gids);
 UPDATE app.generated_artifacts SET content='{}',title='Contenido retirado',status='redacted' WHERE generation_id=ANY(_gids);
 UPDATE app.generation_runs SET status=CASE WHEN status IN ('queued','running') THEN 'cancelled'
   WHEN status='completed' THEN 'invalidated' ELSE status END WHERE id=ANY(_gids);
 UPDATE app.exams SET status=CASE WHEN status IN ('generating','ready') THEN 'invalidated' ELSE status END,invalidated_at=clock_timestamp() WHERE id=ANY(_exams);
 UPDATE app.exam_attempts SET status='invalidated',score_percent=NULL WHERE exam_id=ANY(_exams);
 UPDATE app.roadmaps SET status='stale' WHERE course_id=cid AND status='active';
 INSERT INTO app.deletion_requests(id,scope,subject_id,requested_by,deadline_at)
 VALUES(rid,'document',did,auth.uid(),clock_timestamp()+interval '1 hour');
 INSERT INTO app.deletion_ledger(id,scope,subject_id,requested_at,expires_at)
 VALUES(rid,'document',did,clock_timestamp(),clock_timestamp()+interval '30 days');
 INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key)
 VALUES(gen_random_uuid(),cid,auth.uid(),'purge',rid,rid::text);
 INSERT INTO app.audit_events(id,course_id,actor_id,action,entity_type,entity_id,request_id,result)
 VALUES(gen_random_uuid(),cid,auth.uid(),'document.withdraw','document',did,rid,'success');
 RETURN rid;
END $$;

-- Run retention once per UTC day even if several schedulers start concurrently.
CREATE TABLE app.maintenance_runs (
 kind text NOT NULL,bucket date NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(kind,bucket)
);
ALTER TABLE app.maintenance_runs OWNER TO tutoria_security;
REVOKE ALL ON app.maintenance_runs FROM PUBLIC;
ALTER FUNCTION auth.scheduler_tick() RENAME TO scheduler_tick_before_retention;
REVOKE ALL ON FUNCTION auth.scheduler_tick_before_retention() FROM PUBLIC,tutoria_worker;
CREATE FUNCTION auth.scheduler_tick() RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE _bucket date;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('tutoria.scheduler',0));
 PERFORM auth.scheduler_tick_before_retention();
 INSERT INTO app.maintenance_runs(kind,bucket) VALUES('retention',(clock_timestamp() AT TIME ZONE 'UTC')::date)
 ON CONFLICT DO NOTHING RETURNING bucket INTO _bucket;
 IF _bucket IS NOT NULL THEN PERFORM auth.retention_cleanup(); END IF;
 DELETE FROM app.maintenance_runs WHERE bucket<(clock_timestamp() AT TIME ZONE 'UTC')::date-90;
END $$;
ALTER FUNCTION auth.scheduler_tick() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.scheduler_tick() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.scheduler_tick() TO tutoria_worker;
