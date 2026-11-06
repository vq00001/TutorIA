CREATE TABLE auth.request_limits (
 bucket bytea NOT NULL CHECK(octet_length(bucket)=32),period_start bigint NOT NULL,
 attempts int NOT NULL CHECK(attempts>0),expires_at timestamptz NOT NULL,PRIMARY KEY(bucket,period_start)
);
REVOKE ALL ON auth.request_limits FROM PUBLIC,tutoria_api,tutoria_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON auth.request_limits TO tutoria_security;
CREATE FUNCTION auth.take_rate_limit(b bytea,maximum int,window_seconds int) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p bigint;n int;
BEGIN
 IF octet_length(b)<>32 OR maximum NOT BETWEEN 1 AND 100 OR window_seconds NOT BETWEEN 1 AND 86400 THEN RAISE EXCEPTION 'INVALID_RATE_LIMIT'; END IF;
 p=floor(extract(epoch FROM clock_timestamp())/window_seconds)::bigint;
 INSERT INTO auth.request_limits(bucket,period_start,attempts,expires_at)
 VALUES(b,p,1,clock_timestamp()+make_interval(secs=>window_seconds*2))
 ON CONFLICT(bucket,period_start) DO UPDATE SET attempts=auth.request_limits.attempts+1 RETURNING attempts INTO n;
 RETURN n<=maximum;
END $$;
ALTER FUNCTION auth.take_rate_limit(bytea,int,int) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.take_rate_limit(bytea,int,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.take_rate_limit(bytea,int,int) TO tutoria_api;

CREATE TABLE app.account_email_changes (
 user_id uuid PRIMARY KEY REFERENCES app.users ON DELETE RESTRICT,
 new_email varchar(254) NOT NULL CHECK(new_email=lower(btrim(new_email))),
 token_id uuid NOT NULL UNIQUE REFERENCES app.account_tokens ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE app.account_email_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.account_email_changes FORCE ROW LEVEL SECURITY;
CREATE POLICY own_email_change ON app.account_email_changes USING(user_id=auth.uid()) WITH CHECK(user_id=auth.uid());
GRANT SELECT,INSERT,UPDATE,DELETE ON app.account_email_changes TO tutoria_api,tutoria_security;
CREATE FUNCTION auth.clear_email_change() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM app.account_email_changes WHERE token_id=OLD.id;
 RETURN OLD;
END $$;
ALTER FUNCTION auth.clear_email_change() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.clear_email_change() FROM PUBLIC;
CREATE TRIGGER email_token_cleanup BEFORE DELETE ON app.account_tokens FOR EACH ROW EXECUTE FUNCTION auth.clear_email_change();
CREATE FUNCTION auth.cleanup_auth_limits() RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DELETE FROM auth.request_limits WHERE expires_at<clock_timestamp()
$$;
ALTER FUNCTION auth.cleanup_auth_limits() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.cleanup_auth_limits() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.cleanup_auth_limits() TO tutoria_worker;

CREATE OR REPLACE FUNCTION auth.consume_token(hash bytea,p text) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 UPDATE app.account_tokens t SET consumed_at=clock_timestamp() FROM app.users u
 WHERE t.user_id=u.id AND t.token_hash=hash AND t.purpose=p AND t.consumed_at IS NULL
 AND t.expires_at>clock_timestamp() AND u.status IN ('pending','active') RETURNING t.user_id
$$;

CREATE FUNCTION app.transition_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE before_state text;after_state text;allowed boolean;
BEGIN
 before_state=to_jsonb(OLD)->>'status';after_state=to_jsonb(NEW)->>'status';
 IF before_state=after_state THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='generation_runs' THEN
  allowed=CASE before_state WHEN 'queued' THEN after_state IN ('running','failed','cancelled')
   WHEN 'running' THEN after_state IN ('completed','failed','cancelled')
   WHEN 'completed' THEN after_state='invalidated' ELSE false END;
 ELSIF TG_TABLE_NAME='exams' THEN
  allowed=(before_state='generating' AND after_state IN ('ready','failed','invalidated')) OR (before_state='ready' AND after_state='invalidated');
 ELSE
  allowed=after_state='invalidated' OR CASE before_state WHEN 'in_progress' THEN after_state='submitted'
   WHEN 'submitted' THEN after_state='grading' WHEN 'grading' THEN after_state IN ('graded','needs_review')
   WHEN 'needs_review' THEN after_state='grading' WHEN 'graded' THEN after_state='grading' ELSE false END;
 END IF;
 IF NOT allowed THEN RAISE EXCEPTION 'INVALID_TRANSITION' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER transition BEFORE UPDATE ON app.generation_runs FOR EACH ROW EXECUTE FUNCTION app.transition_guard();
CREATE TRIGGER transition BEFORE UPDATE ON app.exams FOR EACH ROW EXECUTE FUNCTION app.transition_guard();
CREATE TRIGGER transition BEFORE UPDATE ON app.exam_attempts FOR EACH ROW EXECUTE FUNCTION app.transition_guard();
REVOKE ALL ON FUNCTION app.transition_guard() FROM PUBLIC;

CREATE FUNCTION app.grade_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE maximum numeric;
BEGIN
 SELECT q.max_points INTO maximum FROM app.answers a JOIN app.questions q ON q.id=a.question_id
 WHERE a.id=NEW.answer_id AND a.course_id=NEW.course_id AND a.user_id=NEW.user_id;
 IF maximum IS NULL OR (NEW.status='accepted' AND (NEW.points IS NULL OR NEW.points<0 OR NEW.points>maximum))
 OR (NEW.status='needs_review' AND NEW.points IS NOT NULL) THEN RAISE EXCEPTION 'INVALID_GRADE' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' AND ((NEW.revision=1 AND NEW.supersedes_id IS NOT NULL) OR
  (NEW.revision>1 AND NOT EXISTS(SELECT FROM app.grades p WHERE p.id=NEW.supersedes_id AND p.answer_id=NEW.answer_id
    AND p.course_id=NEW.course_id AND p.user_id=NEW.user_id AND p.revision=NEW.revision-1))) THEN
  RAISE EXCEPTION 'INVALID_GRADE_REVISION' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION app.grade_guard() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION app.grade_guard() FROM PUBLIC;
CREATE TRIGGER grade_range BEFORE INSERT OR UPDATE ON app.grades FOR EACH ROW EXECUTE FUNCTION app.grade_guard();
CREATE FUNCTION app.grade_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (to_jsonb(NEW)-'status'-'supersedes_id') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'supersedes_id')
 OR NEW.status NOT IN(OLD.status,'invalidated')
 OR (NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id AND NEW.supersedes_id IS NOT NULL) THEN
  RAISE EXCEPTION 'IMMUTABLE_GRADE' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app.grade_immutable() FROM PUBLIC;
CREATE TRIGGER grade_immutable BEFORE UPDATE ON app.grades FOR EACH ROW EXECUTE FUNCTION app.grade_immutable();
CREATE TRIGGER attempt_version BEFORE UPDATE ON app.exam_attempts FOR EACH ROW EXECUTE FUNCTION app.touch();

ALTER TABLE app.exams ADD COLUMN evidence_attempt_id uuid;
ALTER TABLE app.exams ADD CONSTRAINT exams_evidence_attempt_fk
 FOREIGN KEY(course_id,user_id,id,evidence_attempt_id)
 REFERENCES app.exam_attempts(course_id,user_id,exam_id,id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
UPDATE app.exams e SET evidence_attempt_id=(SELECT a.id FROM app.exam_attempts a WHERE a.exam_id=e.id AND a.status='graded'
 ORDER BY a.graded_at NULLS LAST,a.attempt_no,a.id LIMIT 1);

CREATE FUNCTION auth.rebuild_mastery_projection(cid uuid,uid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE revision bigint;
BEGIN
 UPDATE app.learning_profiles SET mastery_revision=mastery_revision+1 WHERE course_id=cid AND user_id=uid
 RETURNING mastery_revision INTO revision;
 DELETE FROM app.concept_mastery WHERE course_id=cid AND user_id=uid;
 INSERT INTO app.concept_mastery(course_id,user_id,concept_id,alpha,beta,evidence_count,level,mastery_revision)
 SELECT course_id,user_id,concept_id,1+sum(weight*value),1+sum(weight*(1-value)),count(*),
 CASE WHEN count(*)>=3 AND (1+sum(weight*value))/(2+sum(weight))>=.8 THEN 'mastered'
 WHEN (1+sum(weight*value))/(2+sum(weight))<.6 THEN 'reinforce' ELSE 'developing' END,revision
 FROM app.mastery_events WHERE course_id=cid AND user_id=uid AND valid GROUP BY course_id,user_id,concept_id;
 UPDATE app.roadmaps SET status='stale' WHERE course_id=cid AND user_id=uid AND status='active';
END $$;
ALTER FUNCTION auth.rebuild_mastery_projection(uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.rebuild_mastery_projection(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION auth.manual_review(aid uuid,answerid uuid,points numeric,feedback text,gid uuid,rid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a app.exam_attempts;cid uuid;maximum numeric;previous app.grades;winner uuid;accepted boolean;
 total int;graded int;percent numeric;changed boolean=false;affected bigint;grade_row record;
BEGIN
 SELECT course_id INTO cid FROM app.exam_attempts WHERE id=aid AND user_id=auth.uid();
 IF cid IS NULL OR NOT auth.has_course_role(cid,ARRAY['student','coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
 INSERT INTO app.learning_profiles(course_id,user_id) VALUES(cid,auth.uid()) ON CONFLICT DO NOTHING;
 PERFORM 1 FROM app.learning_profiles WHERE course_id=cid AND user_id=auth.uid() FOR UPDATE;
 SELECT evidence_attempt_id INTO winner FROM app.exams WHERE id=(SELECT exam_id FROM app.exam_attempts WHERE id=aid) FOR UPDATE;
 SELECT * INTO a FROM app.exam_attempts WHERE id=aid AND user_id=auth.uid() FOR UPDATE;
 IF a.status NOT IN ('graded','needs_review') THEN RAISE EXCEPTION 'ATTEMPT_CLOSED' USING ERRCODE='23514'; END IF;
 SELECT q.max_points INTO maximum FROM app.answers ans JOIN app.questions q ON q.id=ans.question_id
 WHERE ans.id=answerid AND ans.attempt_id=aid AND ans.user_id=auth.uid();
 IF maximum IS NULL OR points IS NULL OR points<0 OR points>maximum OR length(feedback)>10000 THEN
 RAISE EXCEPTION 'INVALID_GRADE' USING ERRCODE='23514'; END IF;
 SELECT * INTO previous FROM app.grades WHERE answer_id=answerid ORDER BY revision DESC LIMIT 1;
 IF previous.id IS NULL THEN RAISE EXCEPTION 'INVALID_GRADE' USING ERRCODE='23514'; END IF;
 UPDATE app.exam_attempts SET status='grading' WHERE id=aid;
 INSERT INTO app.grades(id,course_id,user_id,answer_id,revision,points,status,method,feedback,grader_version,supersedes_id)
 VALUES(gid,cid,auth.uid(),answerid,previous.revision+1,points,'accepted','manual',feedback,'manual_self',previous.id);
 UPDATE app.mastery_events SET valid=false WHERE valid AND grade_id IN(SELECT id FROM app.grades WHERE answer_id=answerid);
 GET DIAGNOSTICS affected=ROW_COUNT;changed=affected>0;
 SELECT count(*) INTO total FROM app.questions WHERE exam_id=a.exam_id;
 SELECT count(*),coalesce(bool_and(current.status='accepted'),false),
  100*sum(current.points)/NULLIF(sum(current.max_points),0) INTO graded,accepted,percent FROM (
   SELECT DISTINCT ON(ans.id) g.status,g.points,q.max_points FROM app.answers ans JOIN app.questions q ON q.id=ans.question_id
   JOIN app.grades g ON g.answer_id=ans.id WHERE ans.attempt_id=aid ORDER BY ans.id,g.revision DESC) current;
 accepted=accepted AND graded=total AND total>0;
 IF accepted AND winner IS NULL THEN
  UPDATE app.exams SET evidence_attempt_id=aid WHERE id=a.exam_id;winner=aid;END IF;
 IF accepted AND winner=aid THEN
  FOR grade_row IN SELECT DISTINCT ON(ans.id) gr.*,q.concept_id,q.max_points FROM app.answers ans JOIN app.questions q ON q.id=ans.question_id
   JOIN app.grades gr ON gr.answer_id=ans.id WHERE ans.attempt_id=aid ORDER BY ans.id,gr.revision DESC LOOP
   IF grade_row.status='accepted' AND grade_row.method<>'manual' AND grade_row.grader_version<>'manual_self' THEN
    INSERT INTO app.mastery_events(id,course_id,user_id,concept_id,grade_id,value,weight)
    VALUES(gen_random_uuid(),cid,auth.uid(),grade_row.concept_id,grade_row.id,grade_row.points/grade_row.max_points,CASE WHEN grade_row.method='llm' THEN .5 ELSE 1 END)
    ON CONFLICT(grade_id,concept_id,algorithm_version) DO NOTHING;
    GET DIAGNOSTICS affected=ROW_COUNT;changed=changed OR affected>0;
   END IF;
  END LOOP;
 END IF;
 UPDATE app.exam_attempts SET status=CASE WHEN accepted THEN 'graded' ELSE 'needs_review' END,
  score_percent=CASE WHEN accepted THEN percent ELSE NULL END,graded_at=clock_timestamp() WHERE id=aid;
 IF changed THEN PERFORM auth.rebuild_mastery_projection(cid,auth.uid());END IF;
 INSERT INTO app.audit_events(id,course_id,actor_id,action,entity_type,entity_id,request_id,result)
 VALUES(gen_random_uuid(),cid,auth.uid(),'attempt.manual_review','attempt',aid,rid,'success');
 RETURN gid;
END $$;
ALTER FUNCTION auth.manual_review(uuid,uuid,numeric,text,uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.manual_review(uuid,uuid,numeric,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.manual_review(uuid,uuid,numeric,text,uuid,uuid) TO tutoria_api;

CREATE FUNCTION auth.request_regrade(aid uuid,jid uuid,rid uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a app.exam_attempts;cid uuid;
BEGIN
 SELECT course_id INTO cid FROM app.exam_attempts WHERE id=aid AND user_id=auth.uid();
 IF cid IS NULL OR NOT auth.has_course_role(cid,ARRAY['student','coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501';END IF;
 PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
 SELECT * INTO a FROM app.exam_attempts WHERE id=aid AND user_id=auth.uid() FOR UPDATE;
 IF a.status<>'needs_review' THEN RAISE EXCEPTION 'ATTEMPT_CLOSED' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT FROM app.jobs WHERE kind='grade_attempt' AND target_id=aid AND state IN ('queued','running')) THEN
  RAISE EXCEPTION 'GRADE_IN_PROGRESS' USING ERRCODE='23514';END IF;
 INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key,payload)
 VALUES(jid,cid,auth.uid(),'grade_attempt',aid,jid::text,'{"regrade":true}');
 INSERT INTO app.audit_events(id,course_id,actor_id,action,entity_type,entity_id,request_id,result)
 VALUES(gen_random_uuid(),cid,auth.uid(),'attempt.request_regrade','attempt',aid,rid,'success');
 RETURN jid;
END $$;
ALTER FUNCTION auth.request_regrade(uuid,uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.request_regrade(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.request_regrade(uuid,uuid,uuid) TO tutoria_api;
