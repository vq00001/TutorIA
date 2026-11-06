CREATE TABLE app.mastery_algorithms (
 version text PRIMARY KEY,parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.roadmap_algorithms (
 version text PRIMARY KEY,parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'),
 created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO app.mastery_algorithms(version,parameters) VALUES('beta_v1',
 '{"prior_alpha":1,"prior_beta":1,"mastered_score":"0.80","minimum_evidence":3,"reinforce_below":"0.60","deterministic_weight":1,"llm_weight":"0.5","manual_self_evidence":false}');
INSERT INTO app.roadmap_algorithms(version,parameters) VALUES('toposort_v1',
 '{"level_order":["reinforce","developing","unknown","mastered"],"tie_breakers":["canonical_key","id"],"dependencies":["prerequisite"]}');
ALTER TABLE app.mastery_algorithms OWNER TO tutoria_security;
ALTER TABLE app.roadmap_algorithms OWNER TO tutoria_security;
ALTER TABLE app.mastery_algorithms ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.mastery_algorithms FORCE ROW LEVEL SECURITY;
ALTER TABLE app.roadmap_algorithms ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.roadmap_algorithms FORCE ROW LEVEL SECURITY;
CREATE POLICY catalog_read ON app.mastery_algorithms FOR SELECT USING(auth.uid() IS NOT NULL);
CREATE POLICY catalog_read ON app.roadmap_algorithms FOR SELECT USING(auth.uid() IS NOT NULL);
REVOKE ALL ON app.mastery_algorithms,app.roadmap_algorithms FROM PUBLIC;
GRANT SELECT ON app.mastery_algorithms,app.roadmap_algorithms TO tutoria_api,tutoria_worker;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.mastery_algorithms
 FOR EACH ROW EXECUTE FUNCTION app.immutable_upload_limits();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.roadmap_algorithms
 FOR EACH ROW EXECUTE FUNCTION app.immutable_upload_limits();
ALTER TABLE app.mastery_events ADD FOREIGN KEY(algorithm_version) REFERENCES app.mastery_algorithms(version);
ALTER TABLE app.concept_mastery ADD FOREIGN KEY(algorithm_version) REFERENCES app.mastery_algorithms(version);
ALTER TABLE app.roadmaps ADD FOREIGN KEY(algorithm_version) REFERENCES app.roadmap_algorithms(version);
ALTER TABLE app.learning_goals ADD COLUMN is_default boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX one_default_goal ON app.learning_goals(course_id,user_id) WHERE is_default AND status='active';
ALTER TABLE app.roadmaps ADD COLUMN goal_revision bigint NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX one_roadmap_refresh ON app.jobs(target_id)
 WHERE kind='refresh_roadmap' AND state IN ('queued','running');

CREATE FUNCTION auth.enqueue_roadmaps(cid uuid,uid uuid DEFAULT NULL,gid uuid DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE app.roadmaps SET status='stale' WHERE course_id=cid AND status IN ('active','blocked')
 AND (uid IS NULL OR user_id=uid) AND (gid IS NULL OR goal_id=gid);
 INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key)
 SELECT gen_random_uuid(),g.course_id,g.user_id,'refresh_roadmap',g.id,gen_random_uuid()::text
 FROM app.learning_goals g JOIN app.memberships m ON m.course_id=g.course_id AND m.user_id=g.user_id
 JOIN app.users u ON u.id=g.user_id JOIN app.courses c ON c.id=g.course_id
 WHERE g.course_id=cid AND g.status='active' AND m.status='active' AND u.status='active' AND c.status='active'
 AND (uid IS NULL OR g.user_id=uid) AND (gid IS NULL OR g.id=gid)
 AND EXISTS(SELECT 1 FROM app.goal_concepts WHERE goal_id=g.id)
 ON CONFLICT DO NOTHING;
END $$;
ALTER FUNCTION auth.enqueue_roadmaps(uuid,uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.enqueue_roadmaps(uuid,uuid,uuid) FROM PUBLIC;

CREATE FUNCTION auth.roadmap_course_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF ROW(NEW.corpus_revision,NEW.graph_revision,NEW.status)
 IS DISTINCT FROM ROW(OLD.corpus_revision,OLD.graph_revision,OLD.status) THEN
  PERFORM auth.enqueue_roadmaps(NEW.id);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER roadmap_refresh AFTER UPDATE ON app.courses
 FOR EACH ROW EXECUTE FUNCTION auth.roadmap_course_changed();

CREATE FUNCTION auth.roadmap_mastery_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.mastery_revision IS DISTINCT FROM OLD.mastery_revision THEN
  PERFORM auth.enqueue_roadmaps(NEW.course_id,NEW.user_id);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER roadmap_refresh AFTER UPDATE ON app.learning_profiles
 FOR EACH ROW EXECUTE FUNCTION auth.roadmap_mastery_changed();

CREATE FUNCTION auth.roadmap_goal_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM auth.enqueue_roadmaps(NEW.course_id,NEW.user_id,NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER roadmap_refresh AFTER UPDATE ON app.learning_goals
 FOR EACH ROW EXECUTE FUNCTION auth.roadmap_goal_changed();

CREATE FUNCTION auth.roadmap_targets_added() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE app.learning_goals SET row_version=row_version WHERE id IN(SELECT goal_id FROM inserted_targets);
 RETURN NULL;
END $$;
CREATE FUNCTION auth.roadmap_targets_removed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE app.learning_goals SET row_version=row_version WHERE id IN(SELECT goal_id FROM removed_targets);
 RETURN NULL;
END $$;
CREATE TRIGGER roadmap_targets_added AFTER INSERT ON app.goal_concepts
 REFERENCING NEW TABLE AS inserted_targets FOR EACH STATEMENT EXECUTE FUNCTION auth.roadmap_targets_added();
CREATE TRIGGER roadmap_targets_removed AFTER DELETE ON app.goal_concepts
 REFERENCING OLD TABLE AS removed_targets FOR EACH STATEMENT EXECUTE FUNCTION auth.roadmap_targets_removed();

-- The existing revision trigger runs BEFORE a concept is inserted/approved.
-- Synchronize default selections AFTER the new approved row becomes visible.
CREATE FUNCTION auth.roadmap_concept_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.status='approved' OR (TG_OP='UPDATE' AND OLD.status='approved') THEN
  INSERT INTO app.goal_concepts(course_id,user_id,goal_id,concept_id)
  SELECT g.course_id,g.user_id,g.id,c.id FROM app.learning_goals g JOIN app.concepts c ON c.course_id=g.course_id
  JOIN app.memberships m ON m.course_id=g.course_id AND m.user_id=g.user_id
  JOIN app.users u ON u.id=g.user_id
  WHERE g.course_id=NEW.course_id AND g.is_default AND g.status='active' AND c.status='approved'
  AND m.status='active' AND u.status='active'
  ON CONFLICT DO NOTHING;
  PERFORM auth.enqueue_roadmaps(NEW.course_id);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER roadmap_refresh AFTER INSERT OR UPDATE ON app.concepts
 FOR EACH ROW EXECUTE FUNCTION auth.roadmap_concept_changed();

CREATE FUNCTION app.roadmap_identity() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF ROW(NEW.id,NEW.course_id,NEW.user_id,NEW.goal_id,NEW.version_no,NEW.corpus_revision,NEW.graph_revision,
 NEW.mastery_revision,NEW.goal_revision,NEW.algorithm_version,NEW.parameters,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.course_id,OLD.user_id,OLD.goal_id,OLD.version_no,OLD.corpus_revision,OLD.graph_revision,
 OLD.mastery_revision,OLD.goal_revision,OLD.algorithm_version,OLD.parameters,OLD.created_at) THEN
  RAISE EXCEPTION 'IMMUTABLE_ROADMAP' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable BEFORE UPDATE ON app.roadmaps FOR EACH ROW EXECUTE FUNCTION app.roadmap_identity();
CREATE FUNCTION app.roadmap_step_identity() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF current_user<>'tutoria_security' THEN RAISE EXCEPTION 'IMMUTABLE_ROADMAP' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable BEFORE UPDATE ON app.roadmap_steps FOR EACH ROW EXECUTE FUNCTION app.roadmap_step_identity();
REVOKE DELETE ON app.roadmaps,app.roadmap_steps FROM tutoria_api,tutoria_worker;

ALTER FUNCTION auth.roadmap_course_changed() OWNER TO tutoria_security;
ALTER FUNCTION auth.roadmap_mastery_changed() OWNER TO tutoria_security;
ALTER FUNCTION auth.roadmap_goal_changed() OWNER TO tutoria_security;
ALTER FUNCTION auth.roadmap_targets_added() OWNER TO tutoria_security;
ALTER FUNCTION auth.roadmap_targets_removed() OWNER TO tutoria_security;
ALTER FUNCTION auth.roadmap_concept_changed() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.roadmap_course_changed(),auth.roadmap_mastery_changed(),auth.roadmap_goal_changed(),
 auth.roadmap_targets_added(),auth.roadmap_targets_removed(),auth.roadmap_concept_changed(),
 app.roadmap_identity(),app.roadmap_step_identity() FROM PUBLIC;

-- Legacy published snapshots stay immutable; replace them through the worker.
SELECT auth.enqueue_roadmaps(course_id) FROM (SELECT DISTINCT course_id FROM app.learning_goals) existing;
