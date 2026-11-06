-- Security owner is a NOLOGIN role. Function bodies are fixed and inputs bound.
GRANT INSERT,UPDATE ON app.users,app.auth_sessions,app.account_tokens TO tutoria_security;
GRANT SELECT ON app.auth_sessions,app.account_tokens TO tutoria_security;
GRANT INSERT ON app.courses,app.memberships TO tutoria_security;
GRANT SELECT,UPDATE ON app.jobs TO tutoria_security;
ALTER FUNCTION auth.create_course(uuid,text,text,text) OWNER TO tutoria_security;

CREATE FUNCTION auth.register(uid uuid,nick text,display text,mail text,pass text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 INSERT INTO app.users(id,nickname,display_name,email,password_hash) VALUES(uid,nick,display,mail,pass)
$$;
CREATE FUNCTION auth.login_record(mail text) RETURNS TABLE(id uuid,password_hash text,status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT u.id,u.password_hash,u.status FROM app.users u WHERE lower(u.email)=lower(mail)
$$;
CREATE FUNCTION auth.session_owner(hash bytea) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 UPDATE app.auth_sessions s SET last_seen_at=clock_timestamp()
 FROM app.users u WHERE s.user_id=u.id AND s.token_hash=hash AND s.revoked_at IS NULL
 AND s.expires_at>clock_timestamp() AND s.last_seen_at>clock_timestamp()-interval '24 hours'
 AND u.status IN ('pending','active') RETURNING s.user_id
$$;
CREATE FUNCTION auth.consume_token(hash bytea,p text) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 UPDATE app.account_tokens SET consumed_at=clock_timestamp() WHERE token_hash=hash
 AND purpose=p AND consumed_at IS NULL AND expires_at>clock_timestamp() RETURNING user_id
$$;
CREATE FUNCTION auth.token_account(mail text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT id FROM app.users WHERE email=lower(mail) AND status IN ('active','pending')
$$;
CREATE FUNCTION auth.accept_invite(hash bytea) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i app.course_invites;
BEGIN
 SELECT * INTO i FROM app.course_invites WHERE token_hash=hash;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM 1 FROM app.courses WHERE id=i.course_id AND status='active' FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM app.users WHERE id=auth.uid() AND status='active') THEN RETURN NULL; END IF;
 SELECT * INTO i FROM app.course_invites WHERE id=i.id FOR UPDATE;
 IF i.revoked_at IS NOT NULL OR i.expires_at<=clock_timestamp() OR i.uses>=i.max_uses THEN RETURN NULL; END IF;
 IF EXISTS(SELECT FROM app.memberships WHERE course_id=i.course_id AND user_id=auth.uid() AND status='active') THEN
 RETURN i.course_id; END IF;
 INSERT INTO app.memberships(course_id,user_id,role) VALUES(i.course_id,auth.uid(),'student')
 ON CONFLICT(course_id,user_id) DO UPDATE SET status='active',role='student',left_at=NULL,joined_at=clock_timestamp();
 UPDATE app.course_invites SET uses=uses+1 WHERE id=i.id;
 RETURN i.course_id;
END $$;
GRANT SELECT,UPDATE ON app.course_invites TO tutoria_security;
GRANT UPDATE ON app.memberships TO tutoria_security;

CREATE FUNCTION auth.claim_job(worker text) RETURNS SETOF app.jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.jobs;
BEGIN
 SELECT * INTO j FROM app.jobs WHERE state='queued' AND run_after<=clock_timestamp()
 ORDER BY (kind='purge') DESC,run_after,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE app.jobs SET state='running',attempts=attempts+1,lease_token=gen_random_uuid(),locked_by=worker,
 lease_until=clock_timestamp()+interval '120 seconds' WHERE id=j.id RETURNING * INTO j;
 RETURN NEXT j;
END $$;
CREATE FUNCTION auth.recover_jobs() RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 UPDATE app.jobs SET state=CASE WHEN attempts>=max_attempts OR deadline_at<=clock_timestamp()
 THEN 'failed' ELSE 'queued' END, lease_token=NULL,locked_by=NULL,lease_until=NULL,
 error_code='LEASE_EXPIRED',run_after=clock_timestamp()+interval '10 seconds'
 WHERE state='running' AND lease_until<=clock_timestamp()
$$;
DO $$ DECLARE f text; BEGIN
 FOREACH f IN ARRAY ARRAY['register(uuid,text,text,text,text)','login_record(text)','session_owner(bytea)',
 'consume_token(bytea,text)','token_account(text)','accept_invite(bytea)','claim_job(text)','recover_jobs()'] LOOP
 EXECUTE 'ALTER FUNCTION auth.'||f||' OWNER TO tutoria_security';
 EXECUTE 'REVOKE ALL ON FUNCTION auth.'||f||' FROM PUBLIC';
 END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION auth.register(uuid,text,text,text,text),auth.login_record(text),auth.session_owner(bytea),
 auth.consume_token(bytea,text),auth.token_account(text),auth.accept_invite(bytea) TO tutoria_api;
GRANT EXECUTE ON FUNCTION auth.claim_job(text),auth.recover_jobs() TO tutoria_worker;
