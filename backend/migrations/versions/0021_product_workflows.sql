CREATE TABLE app.user_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES app.users ON DELETE CASCADE,
 course_id uuid REFERENCES app.courses ON DELETE CASCADE,
 action text NOT NULL, entity_id uuid, notification boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), read_at timestamptz
);
CREATE INDEX ON app.user_events(user_id,created_at,id);
ALTER TABLE app.user_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.user_events FORCE ROW LEVEL SECURITY;
CREATE POLICY own_events ON app.user_events USING(user_id=auth.uid() AND
 (course_id IS NULL OR auth.has_course_role(course_id,ARRAY['student','coordinator'])))
 WITH CHECK(user_id=auth.uid() AND (course_id IS NULL OR auth.has_course_role(course_id,ARRAY['student','coordinator'])));
GRANT SELECT,UPDATE(read_at) ON app.user_events TO tutoria_api;
GRANT SELECT,INSERT,DELETE ON app.user_events TO tutoria_security;

CREATE TABLE app.direct_invites (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),course_id uuid NOT NULL REFERENCES app.courses ON DELETE CASCADE,
 recipient_id uuid NOT NULL REFERENCES app.users ON DELETE CASCADE,
 inviter_id uuid REFERENCES app.users ON DELETE SET NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,
 UNIQUE(course_id,recipient_id)
);
ALTER TABLE app.direct_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.direct_invites FORCE ROW LEVEL SECURITY;
CREATE POLICY invited ON app.direct_invites FOR SELECT USING(recipient_id=auth.uid()
 OR auth.has_course_role(course_id,ARRAY['coordinator']));
GRANT SELECT ON app.direct_invites TO tutoria_api;
GRANT SELECT,INSERT,UPDATE ON app.direct_invites TO tutoria_security;

-- Return only public identity within an existing membership; never expose email/password.
CREATE FUNCTION auth.course_members(_cid uuid) RETURNS TABLE(id uuid,nickname text,"displayName" text,role text,status text,"rowVersion" bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT u.id,u.nickname::text,u.display_name::text,m.role,m.status,m.row_version
 FROM app.memberships m JOIN app.users u ON u.id=m.user_id
 WHERE m.course_id=_cid AND m.status='active' AND u.status='active'
 AND auth.has_course_role(_cid,ARRAY['student','coordinator']) ORDER BY u.display_name,u.id
$$;

CREATE FUNCTION auth.leave_course(_cid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM 1 FROM app.courses WHERE id=_cid FOR UPDATE;
 IF NOT auth.has_course_role(_cid,ARRAY['student','coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT FROM app.memberships WHERE course_id=_cid AND user_id=auth.uid() AND role='coordinator')
 AND NOT EXISTS(SELECT FROM app.memberships WHERE course_id=_cid AND user_id<>auth.uid() AND role='coordinator' AND status='active') THEN
  RAISE EXCEPTION 'LAST_COORDINATOR' USING ERRCODE='23514'; END IF;
 UPDATE app.memberships SET status='left',left_at=clock_timestamp() WHERE course_id=_cid AND user_id=auth.uid();
 INSERT INTO app.user_events(user_id,action,entity_id) VALUES(auth.uid(),'course.left',_cid);
END $$;

CREATE FUNCTION auth.invite_member(_cid uuid,_email text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE recipient uuid; invitation uuid;
BEGIN
 PERFORM 1 FROM app.courses WHERE id=_cid AND status='active' FOR UPDATE;
 IF NOT FOUND OR NOT auth.has_course_role(_cid,ARRAY['coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 SELECT id INTO recipient FROM app.users WHERE email=lower(btrim(_email)) AND status='active';
 -- Same public response for unknown accounts and existing members.
 IF recipient IS NULL OR EXISTS(SELECT FROM app.memberships WHERE course_id=_cid AND user_id=recipient AND status='active') THEN RETURN; END IF;
 INSERT INTO app.direct_invites(course_id,recipient_id,inviter_id,expires_at)
 VALUES(_cid,recipient,auth.uid(),clock_timestamp()+interval '7 days')
 ON CONFLICT(course_id,recipient_id) DO UPDATE SET inviter_id=auth.uid(),status='pending',
 created_at=clock_timestamp(),expires_at=clock_timestamp()+interval '7 days' RETURNING id INTO invitation;
 INSERT INTO app.user_events(user_id,action,entity_id,notification) VALUES(recipient,'invitation.received',invitation,true);
END $$;

CREATE FUNCTION auth.my_invitations() RETURNS TABLE(id uuid,"courseId" uuid,"courseName" text,status text,"expiresAt" timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT i.id,i.course_id,c.name::text,i.status,i.expires_at FROM app.direct_invites i JOIN app.courses c ON c.id=i.course_id
 WHERE i.recipient_id=auth.uid() AND c.status='active' AND i.expires_at>clock_timestamp()
 ORDER BY i.created_at DESC,i.id
$$;

CREATE FUNCTION auth.respond_invitation(_id uuid,_accept boolean) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE invitation app.direct_invites;
BEGIN
 SELECT * INTO invitation FROM app.direct_invites WHERE id=_id AND recipient_id=auth.uid();
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM 1 FROM app.courses WHERE id=invitation.course_id AND status='active' FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM app.users WHERE id=auth.uid() AND status='active') THEN RETURN NULL; END IF;
 SELECT * INTO invitation FROM app.direct_invites WHERE id=_id FOR UPDATE;
 IF invitation.expires_at<=clock_timestamp() THEN RETURN NULL; END IF;
 IF invitation.status<>'pending' THEN
  IF invitation.status=(CASE WHEN _accept THEN 'accepted' ELSE 'declined' END) THEN RETURN invitation.course_id; END IF;
  RETURN NULL;
 END IF;
 IF _accept THEN
  INSERT INTO app.memberships(course_id,user_id,role) VALUES(invitation.course_id,auth.uid(),'student')
  ON CONFLICT(course_id,user_id) DO UPDATE SET role='student',status='active',left_at=NULL,joined_at=clock_timestamp()
  WHERE app.memberships.status<>'active';
 END IF;
 UPDATE app.direct_invites SET status=CASE WHEN _accept THEN 'accepted' ELSE 'declined' END WHERE id=_id;
 INSERT INTO app.user_events(user_id,action,entity_id) VALUES(auth.uid(),CASE WHEN _accept THEN 'invitation.accepted' ELSE 'invitation.declined' END,_id);
 RETURN invitation.course_id;
END $$;

-- Safe event vocabulary: no prompts, emails, filenames or model output in feeds.
CREATE FUNCTION auth.product_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cid uuid;uid uuid;eid uuid;verb text;notify boolean:=false;shared boolean:=false;
BEGIN
 IF TG_TABLE_NAME='documents' THEN
  cid=NEW.course_id;uid=NEW.uploaded_by;eid=NEW.id;
  IF TG_OP='INSERT' THEN verb='document.uploaded';
  ELSIF NEW.status IS DISTINCT FROM OLD.status THEN verb='document.withdrawn';shared=true;notify=true;
  ELSIF NEW.active_run_id IS DISTINCT FROM OLD.active_run_id THEN verb='document.ready';shared=true;notify=true;
  ELSE RETURN NULL; END IF;
 ELSIF TG_TABLE_NAME='processing_runs' THEN
  IF NEW.state<>'failed' OR NEW.state IS NOT DISTINCT FROM OLD.state THEN RETURN NULL; END IF;
  cid=NEW.course_id;eid=NEW.document_id;verb='document.failed';shared=true;notify=true;
 ELSIF TG_TABLE_NAME='messages' THEN
  cid=NEW.course_id;uid=NEW.user_id;eid=NEW.id;
  IF NEW.role='user' AND TG_OP='INSERT' THEN verb='llm_query.sent'; ELSE RETURN NULL; END IF;
 ELSIF TG_TABLE_NAME='generation_runs' THEN
  IF NEW.status NOT IN ('completed','failed') OR NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NULL; END IF;
  cid=NEW.course_id;uid=NEW.user_id;eid=NEW.id;verb='generation.'||NEW.status;notify=true;
 ELSIF TG_TABLE_NAME='exam_attempts' THEN
  cid=NEW.course_id;uid=NEW.user_id;eid=NEW.id;
  IF TG_OP='INSERT' THEN verb='exam.started';
  ELSIF NEW.status IS DISTINCT FROM OLD.status THEN verb='exam.'||NEW.status;notify=NEW.status IN ('graded','needs_review');
  ELSE RETURN NULL; END IF;
 ELSIF TG_TABLE_NAME='courses' THEN
  cid=NEW.id;uid=NEW.created_by;eid=NEW.id;
  IF TG_OP='INSERT' THEN verb='course.created';
  ELSIF NEW.name IS DISTINCT FROM OLD.name OR NEW.description IS DISTINCT FROM OLD.description OR NEW.objectives IS DISTINCT FROM OLD.objectives THEN verb='course.updated';shared=true;notify=true;
  ELSE RETURN NULL; END IF;
 ELSIF TG_TABLE_NAME='memberships' THEN
  cid=NEW.course_id;uid=NEW.user_id;eid=NEW.user_id;shared=true;notify=true;
  IF TG_OP='INSERT' THEN verb='course.member_joined';
  ELSIF NEW.status IS DISTINCT FROM OLD.status THEN verb='course.membership_changed';
  ELSIF NEW.role IS DISTINCT FROM OLD.role THEN verb='course.role_changed';
  ELSE RETURN NULL; END IF;
 ELSIF TG_TABLE_NAME='audit_events' THEN
  IF NEW.actor_id IS NULL OR NEW.result<>'success' THEN RETURN NULL; END IF;
  cid=NEW.course_id;uid=NEW.actor_id;eid=NEW.entity_id;verb=NEW.action;
  notify=verb IN ('account.email_changed','account.password_changed','account.password_reset');
 ELSE RETURN NULL;
 END IF;
 IF shared THEN
  INSERT INTO app.user_events(user_id,course_id,action,entity_id,notification)
  SELECT user_id,cid,verb,eid,notify FROM app.memberships WHERE course_id=cid AND status='active';
 ELSIF uid IS NOT NULL THEN
  INSERT INTO app.user_events(user_id,course_id,action,entity_id,notification) VALUES(uid,cid,verb,eid,notify);
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['documents','messages','exam_attempts','courses','memberships'] LOOP
  EXECUTE format('CREATE TRIGGER product_event AFTER INSERT OR UPDATE ON app.%I FOR EACH ROW EXECUTE FUNCTION auth.product_event()',t);
 END LOOP;
END $$;
CREATE TRIGGER product_event AFTER UPDATE ON app.generation_runs FOR EACH ROW EXECUTE FUNCTION auth.product_event();
CREATE TRIGGER product_event AFTER UPDATE ON app.processing_runs FOR EACH ROW EXECUTE FUNCTION auth.product_event();
CREATE TRIGGER product_event AFTER INSERT ON app.audit_events FOR EACH ROW EXECUTE FUNCTION auth.product_event();

DO $$ DECLARE f text; BEGIN
 FOREACH f IN ARRAY ARRAY['course_members(uuid)','leave_course(uuid)','invite_member(uuid,text)',
 'my_invitations()','respond_invitation(uuid,boolean)','product_event()'] LOOP
 EXECUTE 'ALTER FUNCTION auth.'||f||' OWNER TO tutoria_security';
 EXECUTE 'REVOKE ALL ON FUNCTION auth.'||f||' FROM PUBLIC';
 END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION auth.course_members(uuid),auth.leave_course(uuid),auth.invite_member(uuid,text),
 auth.my_invitations(),auth.respond_invitation(uuid,boolean) TO tutoria_api;
