CREATE TABLE app.embedding_spaces (
 id uuid PRIMARY KEY,provider text NOT NULL,model_name text NOT NULL,model_revision text NOT NULL,
 dimension int NOT NULL CHECK(dimension BETWEEN 1 AND 2000),distance text NOT NULL DEFAULT 'cosine' CHECK(distance='cosine'),
 normalization text NOT NULL DEFAULT 'l2' CHECK(normalization='l2'),query_prefix text NOT NULL DEFAULT '',
 document_prefix text NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now(),enabled boolean NOT NULL DEFAULT true,
 UNIQUE(provider,model_name,model_revision,dimension,distance,normalization,query_prefix,document_prefix)
);
CREATE TABLE app.course_embedding_config (
 course_id uuid PRIMARY KEY REFERENCES app.courses ON DELETE RESTRICT,
 active_space_id uuid NOT NULL REFERENCES app.embedding_spaces ON DELETE RESTRICT,
 pending_space_id uuid REFERENCES app.embedding_spaces ON DELETE RESTRICT,
 updated_at timestamptz NOT NULL DEFAULT now(),CHECK(active_space_id IS DISTINCT FROM pending_space_id)
);
CREATE TABLE app.documents (
 id uuid PRIMARY KEY,course_id uuid NOT NULL REFERENCES app.courses ON DELETE RESTRICT,
 uploaded_by uuid REFERENCES app.users ON DELETE RESTRICT,title varchar(255) NOT NULL CHECK(length(btrim(title))>0),
 description text NOT NULL DEFAULT '',authors text[] NOT NULL DEFAULT '{}',
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','deletion_pending')),active_run_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 row_version bigint NOT NULL DEFAULT 1,UNIQUE(course_id,id)
);
CREATE TABLE app.document_versions (
 id uuid PRIMARY KEY,course_id uuid NOT NULL,document_id uuid NOT NULL,version_no int NOT NULL CHECK(version_no>0),
 original_name varchar(255) NOT NULL,mime_type varchar(120) NOT NULL,size_bytes bigint NOT NULL CHECK(size_bytes BETWEEN 1 AND 30000000),
 sha256 bytea NOT NULL CHECK(octet_length(sha256)=32),uploaded_by uuid REFERENCES app.users ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(),superseded_at timestamptz,
 status text NOT NULL DEFAULT 'uploaded' CHECK(status IN ('uploaded','processing','ready','failed','purged')),error_code text,
 FOREIGN KEY(course_id,document_id) REFERENCES app.documents(course_id,id) ON DELETE RESTRICT,
 UNIQUE(document_id,version_no),UNIQUE(course_id,document_id,id)
);
CREATE TABLE app.file_contents (
 version_id uuid PRIMARY KEY,course_id uuid NOT NULL,document_id uuid NOT NULL,bytes bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(course_id,document_id,version_id) REFERENCES app.document_versions(course_id,document_id,id) ON DELETE CASCADE
);
CREATE TABLE app.processing_runs (
 id uuid PRIMARY KEY,course_id uuid NOT NULL,document_id uuid NOT NULL,version_id uuid NOT NULL,
 config_hash bytea NOT NULL CHECK(octet_length(config_hash)=32),parser_version text NOT NULL,
 chunker_version text NOT NULL,embedding_space_id uuid NOT NULL REFERENCES app.embedding_spaces ON DELETE RESTRICT,
 parameters jsonb NOT NULL,state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','ready','failed','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(),started_at timestamptz,finished_at timestamptz,error_code text,
 FOREIGN KEY(course_id,document_id,version_id) REFERENCES app.document_versions(course_id,document_id,id) ON DELETE RESTRICT,
 UNIQUE(version_id,config_hash),UNIQUE(course_id,document_id,id),UNIQUE(course_id,id)
);
ALTER TABLE app.documents ADD FOREIGN KEY(course_id,id,active_run_id)
 REFERENCES app.processing_runs(course_id,document_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE app.parent_chunks (
 id uuid PRIMARY KEY,course_id uuid NOT NULL,document_id uuid NOT NULL,run_id uuid NOT NULL,
 ordinal int NOT NULL CHECK(ordinal>=0),section_path text[] NOT NULL DEFAULT '{}',
 text text NOT NULL CHECK(length(btrim(text))>0),locator jsonb NOT NULL,token_estimate int NOT NULL CHECK(token_estimate>0),
 FOREIGN KEY(course_id,document_id,run_id) REFERENCES app.processing_runs(course_id,document_id,id) ON DELETE CASCADE,
 UNIQUE(run_id,ordinal),UNIQUE(course_id,document_id,run_id,id),UNIQUE(course_id,run_id,id)
);
CREATE TABLE app.child_chunks (
 id uuid PRIMARY KEY,course_id uuid NOT NULL,document_id uuid NOT NULL,run_id uuid NOT NULL,parent_id uuid NOT NULL,
 ordinal int NOT NULL CHECK(ordinal>=0),text text NOT NULL,embedding_text text NOT NULL,
 start_offset int NOT NULL,end_offset int NOT NULL,token_estimate int NOT NULL CHECK(token_estimate>0),
 CHECK(0<=start_offset AND start_offset<end_offset),
 FOREIGN KEY(course_id,document_id,run_id,parent_id) REFERENCES app.parent_chunks(course_id,document_id,run_id,id) ON DELETE CASCADE,
 UNIQUE(run_id,ordinal),UNIQUE(course_id,run_id,id)
);
CREATE TABLE app.chunk_embeddings (
 course_id uuid NOT NULL,run_id uuid NOT NULL,child_id uuid NOT NULL,
 embedding_space_id uuid NOT NULL REFERENCES app.embedding_spaces ON DELETE RESTRICT,embedding vector NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(child_id,embedding_space_id),
 FOREIGN KEY(course_id,run_id,child_id) REFERENCES app.child_chunks(course_id,run_id,id) ON DELETE CASCADE
);
CREATE TABLE app.jobs (
 id uuid PRIMARY KEY,course_id uuid REFERENCES app.courses ON DELETE RESTRICT,
 requested_by uuid REFERENCES app.users ON DELETE RESTRICT,kind text NOT NULL,
 target_id uuid,payload jsonb NOT NULL DEFAULT '{}',idempotency_key text NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
 attempts int NOT NULL DEFAULT 0,max_attempts int NOT NULL DEFAULT 3,
 run_after timestamptz NOT NULL DEFAULT now(),lease_token uuid,locked_by text,lease_until timestamptz,
 error_code text,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),deadline_at timestamptz,
 UNIQUE(kind,idempotency_key),CHECK(attempts>=0 AND max_attempts BETWEEN 1 AND 3),
 CHECK(kind NOT IN ('ingest_document','generate_chat','generate_exam') OR deadline_at IS NOT NULL)
);
CREATE TABLE app.idempotency_requests (
 id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES app.users ON DELETE RESTRICT,scope text NOT NULL,
 key varchar(128) NOT NULL,request_hash bytea NOT NULL CHECK(octet_length(request_hash)=32),
 state text NOT NULL DEFAULT 'processing' CHECK(state IN ('processing','completed')),response_status int,response_body jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL,UNIQUE(user_id,scope,key)
);
CREATE TABLE app.audit_events (
 id uuid PRIMARY KEY,course_id uuid,actor_id uuid REFERENCES app.users ON DELETE RESTRICT,
 action text NOT NULL,entity_type text NOT NULL,entity_id uuid,request_id uuid NOT NULL,
 result text NOT NULL CHECK(result IN ('success','denied','error')),metadata jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.deletion_requests (
 id uuid PRIMARY KEY,scope text NOT NULL CHECK(scope IN ('document','course','account')),subject_id uuid NOT NULL,
 requested_by uuid REFERENCES app.users ON DELETE RESTRICT,state text NOT NULL DEFAULT 'queued'
 CHECK(state IN ('queued','running','completed','failed')),requested_at timestamptz NOT NULL DEFAULT now(),
 deadline_at timestamptz NOT NULL,completed_at timestamptz,error_code text
);
CREATE TABLE app.deletion_ledger (
 id uuid PRIMARY KEY,scope text NOT NULL CHECK(scope IN ('document','course','account')),subject_id uuid NOT NULL,
 requested_at timestamptz NOT NULL,completed_at timestamptz,expires_at timestamptz NOT NULL
);

CREATE FUNCTION app.validate_content() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE n bigint;p text;s uuid;d int;
BEGIN
 IF TG_TABLE_NAME='file_contents' THEN
 SELECT size_bytes INTO n FROM app.document_versions WHERE id=NEW.version_id;
 IF octet_length(NEW.bytes)<>n THEN RAISE EXCEPTION 'SIZE_MISMATCH' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='child_chunks' THEN
 SELECT text INTO p FROM app.parent_chunks WHERE id=NEW.parent_id;
 IF NEW.end_offset>length(p) OR substring(p FROM NEW.start_offset+1 FOR NEW.end_offset-NEW.start_offset)<>NEW.text
 THEN RAISE EXCEPTION 'INVALID_OFFSETS' USING ERRCODE='23514'; END IF;
 ELSE
 SELECT embedding_space_id INTO s FROM app.processing_runs WHERE id=NEW.run_id;
 SELECT dimension INTO d FROM app.embedding_spaces WHERE id=s;
 IF s<>NEW.embedding_space_id OR vector_dims(NEW.embedding)<>d OR vector_norm(NEW.embedding)=0
 THEN RAISE EXCEPTION 'INVALID_VECTOR' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER validate BEFORE INSERT OR UPDATE ON app.file_contents FOR EACH ROW EXECUTE FUNCTION app.validate_content();
CREATE TRIGGER validate BEFORE INSERT OR UPDATE ON app.child_chunks FOR EACH ROW EXECUTE FUNCTION app.validate_content();
CREATE TRIGGER validate BEFORE INSERT OR UPDATE ON app.chunk_embeddings FOR EACH ROW EXECUTE FUNCTION app.validate_content();
CREATE FUNCTION app.validate_publication() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r app.processing_runs;
BEGIN
 IF NEW.active_run_id IS NULL OR NEW.active_run_id IS NOT DISTINCT FROM OLD.active_run_id THEN RETURN NEW; END IF;
 SELECT * INTO r FROM app.processing_runs WHERE id=NEW.active_run_id;
 IF r.state<>'ready' OR NOT EXISTS(SELECT FROM app.parent_chunks WHERE run_id=r.id)
 OR NOT EXISTS(SELECT FROM app.child_chunks WHERE run_id=r.id)
 OR EXISTS(SELECT FROM app.child_chunks c WHERE c.run_id=r.id AND NOT EXISTS
 (SELECT FROM app.chunk_embeddings e WHERE e.child_id=c.id AND e.embedding_space_id=r.embedding_space_id))
 OR NOT EXISTS(SELECT FROM app.course_embedding_config WHERE course_id=NEW.course_id AND active_space_id=r.embedding_space_id)
 THEN RAISE EXCEPTION 'INCOMPLETE_RUN' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER publication BEFORE UPDATE OF active_run_id ON app.documents FOR EACH ROW EXECUTE FUNCTION app.validate_publication();
CREATE FUNCTION app.check_duplicate() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM 1 FROM app.courses WHERE id=NEW.course_id FOR UPDATE;
 IF EXISTS(SELECT FROM app.document_versions WHERE course_id=NEW.course_id AND sha256=NEW.sha256 AND status<>'purged' AND id<>NEW.id)
 THEN RAISE EXCEPTION 'DUPLICATE_DOCUMENT' USING ERRCODE='23505'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER deduplicate BEFORE INSERT ON app.document_versions FOR EACH ROW EXECUTE FUNCTION app.check_duplicate();

CREATE INDEX documents_list ON app.documents(course_id,status,created_at,id);
CREATE INDEX versions_document ON app.document_versions(course_id,document_id,version_no DESC);
CREATE INDEX versions_hash ON app.document_versions(course_id,sha256);
CREATE INDEX runs_document ON app.processing_runs(course_id,document_id,state);
CREATE INDEX parents_run ON app.parent_chunks(course_id,run_id);
CREATE INDEX children_run ON app.child_chunks(course_id,run_id,parent_id);
CREATE INDEX embeddings_space ON app.chunk_embeddings(course_id,embedding_space_id,run_id);
CREATE INDEX jobs_ready ON app.jobs(run_after,created_at,id) WHERE state='queued';
CREATE INDEX jobs_leases ON app.jobs(lease_until) WHERE state='running';
CREATE INDEX idempotency_expiry ON app.idempotency_requests(expires_at);
CREATE INDEX audit_course ON app.audit_events(course_id,created_at DESC);
CREATE INDEX audit_actor ON app.audit_events(actor_id,created_at DESC);
CREATE INDEX deletion_deadline ON app.deletion_requests(state,deadline_at);

GRANT SELECT ON app.embedding_spaces TO tutoria_api,tutoria_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON app.documents,app.document_versions,app.file_contents,app.processing_runs,
 app.parent_chunks,app.child_chunks,app.chunk_embeddings,app.jobs,app.course_embedding_config TO tutoria_api,tutoria_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON app.idempotency_requests TO tutoria_api;
GRANT SELECT,INSERT ON app.audit_events TO tutoria_api,tutoria_worker;
GRANT SELECT,INSERT,UPDATE ON app.deletion_requests,app.deletion_ledger TO tutoria_api,tutoria_worker;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['documents','document_versions','file_contents','processing_runs','parent_chunks','child_chunks','chunk_embeddings','course_embedding_config'] LOOP
 EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY members ON app.%I USING(auth.has_course_role(course_id,ARRAY[''student'',''coordinator''])) WITH CHECK(auth.has_course_role(course_id,ARRAY[''student'',''coordinator'']))',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['documents','course_embedding_config','jobs'] LOOP
 EXECUTE format('CREATE TRIGGER touch BEFORE UPDATE ON app.%I FOR EACH ROW EXECUTE FUNCTION app.touch()',t);
 END LOOP;
END $$;
ALTER TABLE app.idempotency_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.idempotency_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY own_request ON app.idempotency_requests USING(user_id=auth.uid()) WITH CHECK(user_id=auth.uid());
ALTER TABLE app.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY own_job ON app.jobs TO tutoria_api USING(requested_by=auth.uid())
 WITH CHECK(requested_by=auth.uid() AND auth.has_course_role(course_id,ARRAY['student','coordinator']));
-- Queue claims are a narrow security-definer function; worker cannot SELECT other jobs directly.
CREATE POLICY worker_job ON app.jobs TO tutoria_worker USING(requested_by=auth.uid()) WITH CHECK(requested_by=auth.uid());
