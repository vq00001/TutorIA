CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA app;
CREATE SCHEMA auth;
REVOKE ALL ON SCHEMA app,auth FROM PUBLIC;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='tutoria_api') THEN
    CREATE ROLE tutoria_api NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='tutoria_worker') THEN
    CREATE ROLE tutoria_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='tutoria_security') THEN
    CREATE ROLE tutoria_security NOLOGIN NOSUPERUSER BYPASSRLS;
  END IF;
END $$;

CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
SET search_path=pg_catalog AS $$
 SELECT NULLIF(current_setting('app.user_id',true),'')::uuid
$$;

CREATE TABLE app.users (
 id uuid PRIMARY KEY, nickname varchar(50) NOT NULL CHECK(nickname ~ '^[a-zA-Z0-9_]{3,50}$'),
 display_name varchar(100) NOT NULL CHECK(length(btrim(display_name)) BETWEEN 1 AND 100),
 email varchar(254) NOT NULL CHECK(email=lower(btrim(email))), password_hash text NOT NULL,
 email_verified_at timestamptz, status text NOT NULL DEFAULT 'pending'
 CHECK(status IN ('pending','active','suspended','deletion_pending')),
 timezone varchar(64) NOT NULL DEFAULT 'America/Santiago',
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 row_version bigint NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX users_email ON app.users(lower(email));
CREATE UNIQUE INDEX users_nickname ON app.users(lower(nickname));
CREATE TABLE app.auth_sessions (
 id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES app.users ON DELETE RESTRICT,
 token_hash bytea NOT NULL UNIQUE CHECK(octet_length(token_hash)=32),
 created_at timestamptz NOT NULL DEFAULT now(),last_seen_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,revoked_at timestamptz,
 CHECK(expires_at>created_at)
);
CREATE INDEX sessions_user ON app.auth_sessions(user_id);
CREATE INDEX sessions_expiry ON app.auth_sessions(expires_at);
CREATE TABLE app.account_tokens (
 id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES app.users ON DELETE RESTRICT,
 purpose text NOT NULL CHECK(purpose IN ('verify_email','reset_password')),
 token_hash bytea NOT NULL UNIQUE CHECK(octet_length(token_hash)=32),
 created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL,
 consumed_at timestamptz, CHECK(expires_at>created_at)
);
CREATE INDEX tokens_user ON app.account_tokens(user_id,purpose);
CREATE TABLE app.policy_acceptances (
 id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES app.users ON DELETE RESTRICT,
 purpose text NOT NULL CHECK(purpose IN ('service_terms','ai_notice','optional_research')),
 policy_version varchar(40) NOT NULL,action text NOT NULL CHECK(action IN ('accepted','withdrawn')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.courses (
 id uuid PRIMARY KEY,name varchar(100) NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 100),
 description text NOT NULL DEFAULT '',objectives text NOT NULL DEFAULT '',subject varchar(150) NOT NULL DEFAULT '',
 color varchar(30) NOT NULL DEFAULT 'mauve' CHECK(color IN ('mauve','blue','green','peach','pink','yellow','teal','lavender')),
 icon varchar(16) NOT NULL DEFAULT '✦',created_by uuid REFERENCES app.users ON DELETE RESTRICT,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived','deletion_pending')),
 corpus_revision bigint NOT NULL DEFAULT 0,graph_revision bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 row_version bigint NOT NULL DEFAULT 1
);
CREATE TABLE app.memberships (
 course_id uuid NOT NULL REFERENCES app.courses ON DELETE RESTRICT,
 user_id uuid NOT NULL REFERENCES app.users ON DELETE RESTRICT,
 role text NOT NULL CHECK(role IN ('student','coordinator')),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','left','removed')),
 joined_at timestamptz NOT NULL DEFAULT now(),left_at timestamptz,row_version bigint NOT NULL DEFAULT 1,
 PRIMARY KEY(course_id,user_id)
);
CREATE INDEX memberships_user ON app.memberships(user_id,status,course_id);
CREATE TABLE app.course_invites (
 id uuid PRIMARY KEY,course_id uuid NOT NULL REFERENCES app.courses ON DELETE RESTRICT,
 token_hash bytea NOT NULL UNIQUE CHECK(octet_length(token_hash)=32),
 created_by uuid REFERENCES app.users ON DELETE RESTRICT,created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,max_uses int NOT NULL DEFAULT 1,uses int NOT NULL DEFAULT 0,
 revoked_at timestamptz,CHECK(uses>=0 AND uses<=max_uses AND max_uses>0)
);
CREATE TABLE app.course_preferences (
 course_id uuid NOT NULL,user_id uuid NOT NULL,graph_positions jsonb NOT NULL DEFAULT '{}',
 updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(course_id,user_id),
 FOREIGN KEY(course_id,user_id) REFERENCES app.memberships ON DELETE RESTRICT,
 CHECK(jsonb_typeof(graph_positions)='object' AND octet_length(graph_positions::text)<=1000000)
);

GRANT USAGE ON SCHEMA app,auth TO tutoria_api,tutoria_worker,tutoria_security;
GRANT SELECT ON app.memberships,app.users,app.courses TO tutoria_security;
CREATE FUNCTION auth.has_course_role(cid uuid,roles text[]) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.memberships m JOIN app.users u ON u.id=m.user_id
 JOIN app.courses c ON c.id=m.course_id WHERE m.course_id=cid AND m.user_id=auth.uid()
 AND m.status='active' AND m.role=ANY(roles) AND u.status='active'
 AND c.status IN ('active','archived'))
$$;
ALTER FUNCTION auth.has_course_role(uuid,text[]) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.has_course_role(uuid,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.has_course_role(uuid,text[]) TO tutoria_api,tutoria_worker;

CREATE FUNCTION app.touch() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF to_jsonb(NEW) ? 'updated_at' THEN NEW.updated_at=clock_timestamp(); END IF;
 IF to_jsonb(NEW) ? 'row_version' THEN NEW.row_version=OLD.row_version+1; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app.require_coordinator() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE cid uuid;
BEGIN
 IF TG_TABLE_NAME='courses' THEN cid=NEW.id; ELSE cid=coalesce(NEW.course_id,OLD.course_id); END IF;
 PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
 IF EXISTS(SELECT FROM app.courses WHERE id=cid AND status IN ('active','archived'))
 AND NOT EXISTS(SELECT FROM app.memberships WHERE course_id=cid AND status='active' AND role='coordinator')
 THEN RAISE EXCEPTION 'LAST_COORDINATOR' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER course_coordinator AFTER INSERT OR UPDATE ON app.courses
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.require_coordinator();
CREATE CONSTRAINT TRIGGER membership_coordinator AFTER INSERT OR UPDATE OR DELETE ON app.memberships
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.require_coordinator();

ALTER TABLE app.courses ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.courses FORCE ROW LEVEL SECURITY;
CREATE POLICY courses_read ON app.courses FOR SELECT USING(auth.has_course_role(id,ARRAY['student','coordinator']));
CREATE POLICY courses_insert ON app.courses FOR INSERT WITH CHECK(created_by=auth.uid());
CREATE POLICY courses_update ON app.courses FOR UPDATE USING(auth.has_course_role(id,ARRAY['coordinator']))
 WITH CHECK(auth.has_course_role(id,ARRAY['coordinator']));
ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY members_read ON app.memberships FOR SELECT
 USING(user_id=auth.uid() OR auth.has_course_role(course_id,ARRAY['coordinator']));
CREATE POLICY members_write ON app.memberships FOR ALL
 USING(auth.has_course_role(course_id,ARRAY['coordinator']))
 WITH CHECK(auth.has_course_role(course_id,ARRAY['coordinator']));
-- Course creation and invite acceptance use narrow security functions: no open INSERT policy.
CREATE FUNCTION auth.create_course(cid uuid,title text,descr text,objs text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT FROM app.users WHERE id=auth.uid() AND status='active') THEN
 RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 INSERT INTO app.courses(id,name,description,objectives,created_by) VALUES(cid,title,descr,objs,auth.uid());
 INSERT INTO app.memberships(course_id,user_id,role) VALUES(cid,auth.uid(),'coordinator');
END $$;
-- Owned by migrator: explicit and narrow audited bootstrap exception.
REVOKE ALL ON FUNCTION auth.create_course(uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.create_course(uuid,text,text,text) TO tutoria_api;

ALTER TABLE app.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.users FORCE ROW LEVEL SECURITY;
CREATE POLICY own_user ON app.users USING(id=auth.uid()) WITH CHECK(id=auth.uid());
ALTER TABLE app.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.auth_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY own_session ON app.auth_sessions USING(user_id=auth.uid()) WITH CHECK(user_id=auth.uid());
ALTER TABLE app.account_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.account_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY own_token ON app.account_tokens USING(user_id=auth.uid()) WITH CHECK(user_id=auth.uid());
ALTER TABLE app.policy_acceptances ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.policy_acceptances FORCE ROW LEVEL SECURITY;
CREATE POLICY own_policy ON app.policy_acceptances USING(user_id=auth.uid()) WITH CHECK(user_id=auth.uid());
ALTER TABLE app.course_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.course_invites FORCE ROW LEVEL SECURITY;
CREATE POLICY invites ON app.course_invites USING(auth.has_course_role(course_id,ARRAY['coordinator']))
 WITH CHECK(auth.has_course_role(course_id,ARRAY['coordinator']));
ALTER TABLE app.course_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.course_preferences FORCE ROW LEVEL SECURITY;
CREATE POLICY own_preferences ON app.course_preferences
 USING(user_id=auth.uid() AND auth.has_course_role(course_id,ARRAY['student','coordinator']))
 WITH CHECK(user_id=auth.uid() AND auth.has_course_role(course_id,ARRAY['student','coordinator']));

GRANT SELECT,INSERT,UPDATE ON app.users,app.auth_sessions,app.account_tokens TO tutoria_api;
GRANT SELECT,INSERT ON app.policy_acceptances TO tutoria_api;
GRANT SELECT,UPDATE ON app.courses TO tutoria_api,tutoria_worker;
GRANT SELECT,INSERT,UPDATE ON app.memberships,app.course_invites,app.course_preferences TO tutoria_api;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['users','courses','memberships','course_preferences'] LOOP
 EXECUTE format('CREATE TRIGGER touch BEFORE UPDATE ON app.%I FOR EACH ROW EXECUTE FUNCTION app.touch()',t);
 END LOOP;
END $$;
