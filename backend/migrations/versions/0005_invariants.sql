CREATE FUNCTION app.graph_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE cycle boolean;
BEGIN
 PERFORM 1 FROM app.courses WHERE id=NEW.course_id FOR UPDATE;
 IF NEW.status='approved' THEN
   IF (SELECT count(*) FROM app.concepts WHERE course_id=NEW.course_id AND id IN(NEW.source_id,NEW.target_id) AND status='approved')<>2
   THEN RAISE EXCEPTION 'CONCEPT_NOT_APPROVED' USING ERRCODE='23514'; END IF;
   IF NEW.relation IN ('prerequisite','part_of') THEN
     WITH RECURSIVE reachable(id) AS (
       SELECT NEW.target_id UNION
       SELECT e.target_id FROM app.concept_edges e JOIN reachable r ON e.source_id=r.id
       WHERE e.course_id=NEW.course_id AND e.relation=NEW.relation AND e.status='approved' AND e.id<>NEW.id
     ) SELECT EXISTS(SELECT FROM reachable WHERE id=NEW.source_id) INTO cycle;
     IF cycle THEN RAISE EXCEPTION 'GRAPH_CYCLE' USING ERRCODE='23514'; END IF;
   END IF;
 END IF;
 IF NEW.status='approved' OR (TG_OP='UPDATE' AND OLD.status='approved') THEN
   UPDATE app.courses SET graph_revision=graph_revision+1 WHERE id=NEW.course_id;
   UPDATE app.roadmaps SET status='stale' WHERE course_id=NEW.course_id AND status='active';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER graph_guard BEFORE INSERT OR UPDATE ON app.concept_edges FOR EACH ROW EXECUTE FUNCTION app.graph_guard();
CREATE FUNCTION app.concept_revision() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.status='approved' OR (TG_OP='UPDATE' AND OLD.status='approved') THEN
  PERFORM 1 FROM app.courses WHERE id=NEW.course_id FOR UPDATE;
  UPDATE app.courses SET graph_revision=graph_revision+1 WHERE id=NEW.course_id;
  UPDATE app.roadmaps SET status='stale' WHERE course_id=NEW.course_id AND status='active';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER revision BEFORE INSERT OR UPDATE ON app.concepts FOR EACH ROW EXECUTE FUNCTION app.concept_revision();

CREATE FUNCTION app.answer_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a app.exam_attempts;aid uuid;
BEGIN
 IF TG_TABLE_NAME='answer_selections' THEN
 SELECT attempt_id INTO aid FROM app.answers WHERE id=NEW.answer_id;
 ELSE aid=NEW.attempt_id; END IF;
 SELECT * INTO a FROM app.exam_attempts WHERE id=aid FOR UPDATE;
 IF a.status<>'in_progress' OR a.deadline_at<=clock_timestamp() THEN
 RAISE EXCEPTION 'ATTEMPT_CLOSED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard BEFORE INSERT OR UPDATE ON app.answers FOR EACH ROW EXECUTE FUNCTION app.answer_guard();
CREATE TRIGGER guard BEFORE INSERT OR UPDATE ON app.answer_selections FOR EACH ROW EXECUTE FUNCTION app.answer_guard();

CREATE FUNCTION app.exam_publication() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE q app.questions;n int;opts int;keys int;
BEGIN
 IF NEW.status<>'ready' OR OLD.status='ready' THEN RETURN NEW; END IF;
 SELECT count(*) INTO n FROM app.questions WHERE exam_id=NEW.id;
 IF n NOT BETWEEN 1 AND 20 OR (SELECT max(position) FROM app.questions WHERE exam_id=NEW.id)<>n
 THEN RAISE EXCEPTION 'INVALID_EXAM' USING ERRCODE='23514'; END IF;
 FOR q IN SELECT * FROM app.questions WHERE exam_id=NEW.id LOOP
  IF NOT EXISTS(SELECT FROM app.concepts WHERE id=q.concept_id AND course_id=q.course_id AND status='approved')
  OR NOT EXISTS(SELECT FROM app.question_sources qs JOIN app.documents d ON d.course_id=qs.course_id
     JOIN app.parent_chunks p ON p.id=qs.parent_id AND p.document_id=d.id
     WHERE qs.question_id=q.id AND qs.run_id=d.active_run_id AND d.status='active')
  OR NOT EXISTS(SELECT FROM app.question_keys WHERE question_id=q.id)
  THEN RAISE EXCEPTION 'EXAM_SOURCE_REQUIRED' USING ERRCODE='23514'; END IF;
  SELECT count(*) INTO opts FROM app.question_options WHERE question_id=q.id;
  SELECT count(*) INTO keys FROM app.correct_options WHERE question_id=q.id;
  IF (q.kind='development' AND (opts<>0 OR NOT EXISTS(SELECT FROM app.question_keys WHERE question_id=q.id AND expected_answer IS NOT NULL AND rubric IS NOT NULL)))
  OR (q.kind IN ('alternatives','multiple') AND opts NOT BETWEEN 2 AND 6)
  OR (q.kind='true_false' AND opts<>2)
  OR (q.kind IN ('alternatives','true_false') AND keys<>1)
  OR (q.kind='multiple' AND keys<1)
  THEN RAISE EXCEPTION 'INVALID_QUESTION' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER publication BEFORE UPDATE OF status ON app.exams FOR EACH ROW EXECUTE FUNCTION app.exam_publication();

CREATE FUNCTION app.interval_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM 1 FROM app.study_sessions WHERE id=NEW.session_id FOR UPDATE;
 IF EXISTS(SELECT FROM app.study_intervals WHERE session_id=NEW.session_id
 AND start_at<NEW.end_at AND end_at>NEW.start_at) THEN
 RAISE EXCEPTION 'INTERVAL_OVERLAP' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER overlap BEFORE INSERT ON app.study_intervals FOR EACH ROW EXECUTE FUNCTION app.interval_guard();

CREATE FUNCTION app.run_state_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE allowed boolean;
BEGIN
 IF NEW.state=OLD.state THEN RETURN NEW; END IF;
 allowed=CASE OLD.state WHEN 'queued' THEN NEW.state IN ('running','cancelled')
 WHEN 'running' THEN NEW.state IN ('ready','failed','cancelled') WHEN 'failed' THEN NEW.state='queued' ELSE false END;
 IF NOT allowed THEN RAISE EXCEPTION 'INVALID_TRANSITION' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER state_guard BEFORE UPDATE ON app.processing_runs FOR EACH ROW EXECUTE FUNCTION app.run_state_guard();
CREATE FUNCTION app.document_state_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.status='deletion_pending' AND NEW.status<>'deletion_pending' THEN
 RAISE EXCEPTION 'INVALID_TRANSITION' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER state_guard BEFORE UPDATE ON app.documents FOR EACH ROW EXECUTE FUNCTION app.document_state_guard();

-- Shared reads are distinct from coordinator-only graph writes.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['concepts','concept_aliases','concept_sources','concept_edges','edge_sources'] LOOP
 EXECUTE format('DROP POLICY scope ON app.%I',t);
 EXECUTE format('CREATE POLICY shared_read ON app.%I FOR SELECT USING(auth.has_course_role(course_id,ARRAY[''student'',''coordinator'']))',t);
 EXECUTE format('CREATE POLICY coordinator_write ON app.%I FOR ALL USING(auth.has_course_role(course_id,ARRAY[''coordinator''])) WITH CHECK(auth.has_course_role(course_id,ARRAY[''coordinator'']))',t);
 END LOOP;
END $$;

CREATE FUNCTION auth.review_keys(aid uuid) RETURNS TABLE(question_id uuid,expected_answer text,explanation text,option_ids uuid[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT k.question_id,k.expected_answer,k.explanation,ARRAY(SELECT option_id FROM app.correct_options WHERE question_id=k.question_id)
 FROM app.question_keys k JOIN app.exam_attempts a ON a.exam_id=k.exam_id AND a.user_id=k.user_id AND a.course_id=k.course_id
 WHERE a.id=aid AND a.user_id=auth.uid() AND a.status IN ('graded','needs_review')
 AND auth.has_course_role(a.course_id,ARRAY['student','coordinator'])
$$;
GRANT SELECT ON app.question_keys,app.correct_options,app.exam_attempts TO tutoria_security;
ALTER FUNCTION auth.review_keys(uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.review_keys(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.review_keys(uuid) TO tutoria_api;
