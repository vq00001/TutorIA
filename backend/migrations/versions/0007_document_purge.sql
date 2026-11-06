REVOKE ALL ON app.deletion_ledger FROM tutoria_api;
REVOKE INSERT,UPDATE ON app.deletion_requests FROM tutoria_api;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA app TO tutoria_security;
REVOKE UPDATE,DELETE ON app.audit_events,app.policy_acceptances FROM tutoria_security;

CREATE FUNCTION auth.request_document_delete(cid uuid,did uuid,rid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d app.documents;
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
 UPDATE app.documents SET status='deletion_pending',active_run_id=NULL WHERE id=did;
 UPDATE app.courses SET corpus_revision=corpus_revision+1 WHERE id=cid;
 INSERT INTO app.deletion_requests(id,scope,subject_id,requested_by,deadline_at)
 VALUES(rid,'document',did,auth.uid(),clock_timestamp()+interval '1 hour');
 INSERT INTO app.deletion_ledger(id,scope,subject_id,requested_at,expires_at)
 VALUES(rid,'document',did,clock_timestamp(),clock_timestamp()+interval '30 days');
 UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL WHERE course_id=cid AND state IN ('queued','running')
 AND (target_id IN(SELECT id FROM app.processing_runs WHERE document_id=did)
 OR target_id IN(SELECT generation_id FROM app.generation_sources WHERE parent_id IN(SELECT id FROM app.parent_chunks WHERE document_id=did)));
 UPDATE app.messages SET parts='[]',status='redacted',redacted_at=clock_timestamp()
 WHERE generation_id IN(SELECT generation_id FROM app.generation_sources WHERE parent_id IN(SELECT id FROM app.parent_chunks WHERE document_id=did));
 UPDATE app.generation_runs SET status='invalidated' WHERE id IN
 (SELECT generation_id FROM app.generation_sources WHERE parent_id IN(SELECT id FROM app.parent_chunks WHERE document_id=did));
 UPDATE app.exams SET status='invalidated',invalidated_at=clock_timestamp() WHERE id IN
 (SELECT exam_id FROM app.questions WHERE id IN(SELECT question_id FROM app.question_sources WHERE parent_id IN(SELECT id FROM app.parent_chunks WHERE document_id=did)));
 UPDATE app.exam_attempts SET status='invalidated',score_percent=NULL WHERE exam_id IN(SELECT id FROM app.exams WHERE status='invalidated' AND course_id=cid);
 UPDATE app.roadmaps SET status='stale' WHERE course_id=cid AND status='active';
 INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key)
 VALUES(gen_random_uuid(),cid,auth.uid(),'purge',rid,rid::text);
 INSERT INTO app.audit_events(id,course_id,actor_id,action,entity_type,entity_id,request_id,result)
 VALUES(gen_random_uuid(),cid,auth.uid(),'document.withdraw','document',did,rid,'success');
 RETURN rid;
END $$;

CREATE FUNCTION auth.purge_document(rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.deletion_requests;did uuid;cid uuid;parents uuid[];exams uuid[];gids uuid[];u uuid;v uuid;
BEGIN
 SELECT * INTO r FROM app.deletion_requests WHERE id=rid AND scope='document' FOR UPDATE;
 IF r.id IS NULL THEN RAISE EXCEPTION 'INVALID_DELETION_REQUEST'; END IF;
 did=r.subject_id;
 SELECT course_id INTO cid FROM app.documents WHERE id=did;
 IF cid IS NULL THEN RETURN jsonb_build_object('id',rid,'scope','document','subjectId',did,'requestedAt',r.requested_at); END IF;
 PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
 PERFORM 1 FROM app.documents WHERE id=did FOR UPDATE;
 UPDATE app.deletion_requests SET state='running' WHERE id=rid;
 SELECT array_agg(id) INTO parents FROM app.parent_chunks WHERE document_id=did;
 SELECT array_agg(DISTINCT generation_id) INTO gids FROM app.generation_sources WHERE parent_id=ANY(parents);
 SELECT array_agg(DISTINCT q.exam_id) INTO exams FROM app.questions q JOIN app.question_sources s ON s.question_id=q.id WHERE s.parent_id=ANY(parents);
 UPDATE app.messages SET parts='[]',status='redacted',redacted_at=clock_timestamp() WHERE generation_id=ANY(gids);
 UPDATE app.generated_artifacts SET content='{}',title='Contenido retirado',status='redacted' WHERE generation_id=ANY(gids);
 UPDATE app.citations SET parent_id=NULL,label='Fuente retirada',locator='{}',status='source_removed' WHERE parent_id=ANY(parents);
 DELETE FROM app.generation_sources WHERE parent_id=ANY(parents);
 UPDATE app.generation_runs SET status='invalidated',parameters='{}',reserved_cost_usd=0 WHERE id=ANY(gids);
 UPDATE app.exams SET status='invalidated',title='Evaluación retirada',invalidated_at=clock_timestamp() WHERE id=ANY(exams);
 UPDATE app.exam_attempts SET status='invalidated',score_percent=NULL WHERE exam_id=ANY(exams);
 UPDATE app.mastery_events SET valid=false WHERE grade_id IN(SELECT g.id FROM app.grades g JOIN app.answers a ON a.id=g.answer_id WHERE a.exam_id=ANY(exams));
 DELETE FROM app.mastery_events WHERE grade_id IN(SELECT g.id FROM app.grades g JOIN app.answers a ON a.id=g.answer_id WHERE a.exam_id=ANY(exams));
 UPDATE app.grades SET supersedes_id=NULL WHERE answer_id IN(SELECT id FROM app.answers WHERE exam_id=ANY(exams));
 DELETE FROM app.grades WHERE answer_id IN(SELECT id FROM app.answers WHERE exam_id=ANY(exams));
 DELETE FROM app.answer_selections WHERE exam_id=ANY(exams);
 DELETE FROM app.answers WHERE exam_id=ANY(exams);
 DELETE FROM app.correct_options WHERE exam_id=ANY(exams);
 DELETE FROM app.question_keys WHERE exam_id=ANY(exams);
 DELETE FROM app.question_options WHERE exam_id=ANY(exams);
 DELETE FROM app.question_sources WHERE question_id IN(SELECT id FROM app.questions WHERE exam_id=ANY(exams));
 UPDATE app.questions SET prompt='Pregunta retirada por eliminación de fuente' WHERE exam_id=ANY(exams);
 DELETE FROM app.concept_sources WHERE parent_id=ANY(parents);
 DELETE FROM app.edge_sources WHERE parent_id=ANY(parents);
 UPDATE app.concepts c SET status='retired',description=CASE WHEN description_origin='llm' THEN '' ELSE description END,
 label=CASE WHEN label_origin='llm' THEN 'Concepto retirado' ELSE label END
 WHERE course_id=cid AND origin='llm' AND NOT EXISTS(SELECT FROM app.concept_sources s WHERE s.concept_id=c.id);
 UPDATE app.concept_edges e SET status='retired' WHERE course_id=cid AND (origin='llm' AND NOT EXISTS(SELECT FROM app.edge_sources s WHERE s.edge_id=e.id)
 OR source_id IN(SELECT id FROM app.concepts WHERE course_id=cid AND status='retired') OR target_id IN(SELECT id FROM app.concepts WHERE course_id=cid AND status='retired'));
 UPDATE app.roadmaps SET status='stale' WHERE course_id=cid AND status='active';
 UPDATE app.roadmap_steps SET reason='Fuente retirada' WHERE course_id=cid;
 DELETE FROM app.concept_mastery WHERE course_id=cid;
 INSERT INTO app.concept_mastery(course_id,user_id,concept_id,alpha,beta,evidence_count,level)
 SELECT course_id,user_id,concept_id,1+sum(weight*value),1+sum(weight*(1-value)),count(*),
 CASE WHEN (1+sum(weight*value))/(2+sum(weight))>=.8 AND count(*)>=3 THEN 'mastered'
 WHEN (1+sum(weight*value))/(2+sum(weight))<.6 THEN 'reinforce' ELSE 'developing' END
 FROM app.mastery_events WHERE course_id=cid AND valid GROUP BY course_id,user_id,concept_id;
 UPDATE app.learning_profiles SET mastery_revision=mastery_revision+1 WHERE course_id=cid;
 DELETE FROM app.idempotency_requests WHERE response_body::text LIKE '%'||did::text||'%';
 UPDATE app.documents SET active_run_id=NULL WHERE id=did;
 DELETE FROM app.chunk_embeddings WHERE run_id IN(SELECT id FROM app.processing_runs WHERE document_id=did);
 DELETE FROM app.child_chunks WHERE document_id=did;
 DELETE FROM app.parent_chunks WHERE document_id=did;
 DELETE FROM app.processing_runs WHERE document_id=did;
 DELETE FROM app.file_contents WHERE document_id=did;
 DELETE FROM app.document_versions WHERE document_id=did;
 DELETE FROM app.documents WHERE id=did;
 UPDATE app.courses SET corpus_revision=corpus_revision+1 WHERE id=cid;
 RETURN jsonb_build_object('id',rid,'scope','document','subjectId',did,'requestedAt',r.requested_at);
END $$;
CREATE FUNCTION auth.confirm_purge(rid uuid) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 UPDATE app.deletion_ledger SET completed_at=clock_timestamp() WHERE id=rid;
 UPDATE app.deletion_requests SET state='completed',completed_at=clock_timestamp() WHERE id=rid
$$;
ALTER FUNCTION auth.request_document_delete(uuid,uuid,uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.purge_document(uuid) OWNER TO tutoria_security;
ALTER FUNCTION auth.confirm_purge(uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.request_document_delete(uuid,uuid,uuid),auth.purge_document(uuid),auth.confirm_purge(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.request_document_delete(uuid,uuid,uuid) TO tutoria_api;
GRANT EXECUTE ON FUNCTION auth.purge_document(uuid),auth.confirm_purge(uuid) TO tutoria_worker;
ALTER TABLE app.deletion_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.deletion_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY own_deletions ON app.deletion_requests USING(requested_by=auth.uid());
