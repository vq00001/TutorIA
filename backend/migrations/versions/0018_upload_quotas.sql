-- Private receipts preserve the rolling daily quota after originals are purged.
CREATE TABLE auth.upload_limits (
 version text PRIMARY KEY, effective_from timestamptz NOT NULL,
 course_bytes bigint NOT NULL CHECK(course_bytes>0),
 user_daily_bytes bigint NOT NULL CHECK(user_daily_bytes>0)
);
INSERT INTO auth.upload_limits VALUES('upload_limits_v1','2026-01-01T00:00:00Z',2000000000,1000000000);
CREATE TABLE auth.upload_usage (
 version_id uuid PRIMARY KEY,user_id uuid NOT NULL,size_bytes bigint NOT NULL CHECK(size_bytes>0),
 accepted_at timestamptz NOT NULL,limits_version text NOT NULL REFERENCES auth.upload_limits(version)
);
CREATE INDEX upload_usage_user_time ON auth.upload_usage(user_id,accepted_at);
CREATE INDEX upload_usage_time ON auth.upload_usage(accepted_at);
ALTER TABLE auth.upload_limits OWNER TO tutoria_security;
ALTER TABLE auth.upload_usage OWNER TO tutoria_security;
REVOKE ALL ON auth.upload_limits,auth.upload_usage FROM PUBLIC,tutoria_api,tutoria_worker;
-- Preserve the known portion of the rolling window when upgrading a live DB.
INSERT INTO auth.upload_usage(version_id,user_id,size_bytes,accepted_at,limits_version)
 SELECT id,uploaded_by,size_bytes,created_at,'upload_limits_v1'
 FROM app.document_versions WHERE uploaded_by IS NOT NULL
 AND created_at>clock_timestamp()-interval '24 hours';

CREATE FUNCTION app.immutable_upload_limits() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 RAISE EXCEPTION 'IMMUTABLE_CONFIGURATION' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION app.immutable_upload_limits() FROM PUBLIC;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON auth.upload_limits
 FOR EACH ROW EXECUTE FUNCTION app.immutable_upload_limits();

CREATE FUNCTION app.document_version_identity() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF ROW(NEW.id,NEW.course_id,NEW.document_id,NEW.version_no,NEW.size_bytes,NEW.sha256,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.course_id,OLD.document_id,OLD.version_no,OLD.size_bytes,OLD.sha256,OLD.created_at)
 OR (NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
 AND NOT (NEW.uploaded_by IS NULL AND current_user='tutoria_security')) THEN
  RAISE EXCEPTION 'IMMUTABLE_VERSION' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app.document_version_identity() FROM PUBLIC;
CREATE TRIGGER version_identity BEFORE UPDATE ON app.document_versions
 FOR EACH ROW EXECUTE FUNCTION app.document_version_identity();

CREATE FUNCTION auth.reserve_upload() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE limits auth.upload_limits;used numeric;daily numeric;accepted timestamptz;
BEGIN
 IF NEW.uploaded_by IS DISTINCT FROM auth.uid()
 OR NOT auth.has_course_role(NEW.course_id,ARRAY['student','coordinator']) THEN
  RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501';
 END IF;
 PERFORM 1 FROM app.courses WHERE id=NEW.course_id AND status='active' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
 -- Course first, then uploader: all uploads in one request address one course.
 PERFORM pg_advisory_xact_lock(hashtextextended('tutoria.upload.user.'||auth.uid()::text,0));
 accepted=clock_timestamp();
 SELECT * INTO STRICT limits FROM auth.upload_limits WHERE effective_from<=accepted
 ORDER BY effective_from DESC,version DESC LIMIT 1;
 -- SECURITY DEFINER sees retained versions hidden by source withdrawal/RLS.
 SELECT coalesce(sum(size_bytes),0) INTO used FROM app.document_versions
 WHERE course_id=NEW.course_id AND status<>'purged';
 SELECT coalesce(sum(size_bytes),0) INTO daily FROM auth.upload_usage
 WHERE user_id=auth.uid() AND accepted_at>accepted-interval '24 hours';
 IF used+NEW.size_bytes>limits.course_bytes OR daily+NEW.size_bytes>limits.user_daily_bytes THEN
  RAISE EXCEPTION 'UPLOAD_QUOTA_EXCEEDED' USING ERRCODE='23514';
 END IF;
 INSERT INTO auth.upload_usage(version_id,user_id,size_bytes,accepted_at,limits_version)
 VALUES(NEW.id,auth.uid(),NEW.size_bytes,accepted,limits.version);
 RETURN NEW;
END $$;
ALTER FUNCTION auth.reserve_upload() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.reserve_upload() FROM PUBLIC;
CREATE TRIGGER reserve_upload BEFORE INSERT ON app.document_versions
 FOR EACH ROW EXECUTE FUNCTION auth.reserve_upload();

CREATE FUNCTION auth.clear_upload_usage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM auth.upload_usage WHERE user_id=OLD.id;
 RETURN OLD;
END $$;
ALTER FUNCTION auth.clear_upload_usage() OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.clear_upload_usage() FROM PUBLIC;
CREATE TRIGGER clear_upload_usage BEFORE DELETE ON app.users
 FOR EACH ROW EXECUTE FUNCTION auth.clear_upload_usage();

CREATE OR REPLACE FUNCTION auth.cleanup_auth_limits() RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DELETE FROM auth.request_limits WHERE expires_at<clock_timestamp();
 DELETE FROM auth.upload_usage WHERE accepted_at<=clock_timestamp()-interval '24 hours'
$$;
