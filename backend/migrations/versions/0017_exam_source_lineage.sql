-- Preserve source removal for exams without published questions.
CREATE OR REPLACE FUNCTION auth.redact_lineage(_cid uuid,_parents uuid[]) RETURNS void
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
 -- Failed/in-flight exams may have no questions yet, but already consumed sources.
 SELECT array_agg(id) INTO _exams FROM app.exams WHERE course_id=_cid
 AND (id=ANY(_exams) OR generation_id=ANY(_generations));
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
 SELECT array_agg(id) INTO _exams FROM app.exams WHERE course_id=cid
 AND (id=ANY(_exams) OR generation_id=ANY(_gids));
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
 UPDATE app.exams SET status=CASE WHEN status IN ('generating','ready') THEN 'invalidated' ELSE status END,title='Evaluación retirada',invalidated_at=clock_timestamp() WHERE id=ANY(_exams);
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

