-- Row visibility enforces withdrawal even if an application query forgets a filter.
CREATE FUNCTION auth.document_is_active(cid uuid,did uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT auth.has_course_role(cid,ARRAY['student','coordinator']) AND EXISTS(
   SELECT 1 FROM app.documents WHERE course_id=cid AND id=did AND status='active')
$$;
ALTER FUNCTION auth.document_is_active(uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.document_is_active(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.document_is_active(uuid,uuid) TO tutoria_api,tutoria_worker;

CREATE FUNCTION auth.published_run_visible(cid uuid,rid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT auth.has_course_role(cid,ARRAY['student','coordinator']) AND EXISTS(
  SELECT 1 FROM app.processing_runs r JOIN app.documents d ON d.course_id=r.course_id AND d.id=r.document_id
  WHERE r.course_id=cid AND r.id=rid AND r.state='ready' AND d.status='active'
  AND (d.active_run_id=r.id OR EXISTS(
    SELECT 1 FROM app.generation_sources gs JOIN app.generation_runs g
    ON g.course_id=gs.course_id AND g.id=gs.generation_id
    WHERE gs.course_id=cid AND gs.run_id=rid AND g.user_id=auth.uid()
      AND g.status='completed')))
$$;
ALTER FUNCTION auth.published_run_visible(uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.published_run_visible(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.published_run_visible(uuid,uuid) TO tutoria_api;

CREATE FUNCTION auth.run_document_is_active(cid uuid,rid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT auth.has_course_role(cid,ARRAY['student','coordinator']) AND EXISTS(
  SELECT 1 FROM app.processing_runs r JOIN app.documents d ON d.course_id=r.course_id AND d.id=r.document_id
  WHERE r.course_id=cid AND r.id=rid AND d.status='active')
$$;
ALTER FUNCTION auth.run_document_is_active(uuid,uuid) OWNER TO tutoria_security;
REVOKE ALL ON FUNCTION auth.run_document_is_active(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.run_document_is_active(uuid,uuid) TO tutoria_worker;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['document_versions','file_contents','processing_runs','parent_chunks','child_chunks'] LOOP
  EXECUTE format('DROP POLICY worker_write ON app.%I',t);
  EXECUTE format('CREATE POLICY worker_write ON app.%I TO tutoria_worker USING(auth.document_is_active(course_id,document_id)) WITH CHECK(auth.document_is_active(course_id,document_id))',t);
 END LOOP;
END $$;
DROP POLICY worker_write ON app.chunk_embeddings;
CREATE POLICY worker_write ON app.chunk_embeddings TO tutoria_worker
 USING(auth.run_document_is_active(course_id,run_id)) WITH CHECK(auth.run_document_is_active(course_id,run_id));

DROP POLICY document_read ON app.documents;
DROP POLICY document_owner ON app.documents;
DROP POLICY document_worker ON app.documents;
CREATE POLICY document_read ON app.documents FOR SELECT TO tutoria_api
 USING(status='active' AND auth.has_course_role(course_id,ARRAY['student','coordinator']));
CREATE POLICY document_owner ON app.documents FOR UPDATE TO tutoria_api
 USING(status='active' AND auth.has_course_role(course_id,ARRAY['student','coordinator'])
   AND (uploaded_by=auth.uid() OR auth.has_course_role(course_id,ARRAY['coordinator'])))
 WITH CHECK(status='active' AND auth.has_course_role(course_id,ARRAY['student','coordinator'])
   AND (uploaded_by=auth.uid() OR auth.has_course_role(course_id,ARRAY['coordinator'])));
CREATE POLICY document_worker ON app.documents TO tutoria_worker
 USING(status='active' AND auth.has_course_role(course_id,ARRAY['student','coordinator']))
 WITH CHECK(status='active' AND auth.has_course_role(course_id,ARRAY['student','coordinator']));

DROP POLICY read_members ON app.document_versions;
CREATE POLICY active_metadata ON app.document_versions FOR SELECT TO tutoria_api
 USING(auth.document_is_active(course_id,document_id));
DROP POLICY read_members ON app.processing_runs;
CREATE POLICY active_metadata ON app.processing_runs FOR SELECT TO tutoria_api
 USING(auth.document_is_active(course_id,document_id));

DROP POLICY read_members ON app.file_contents;
CREATE POLICY published_original ON app.file_contents FOR SELECT TO tutoria_api
 USING(auth.document_is_active(course_id,document_id) AND EXISTS(
  SELECT 1 FROM app.document_versions v WHERE v.course_id=file_contents.course_id
    AND v.document_id=file_contents.document_id AND v.id=file_contents.version_id AND v.status='ready'));

DROP POLICY read_members ON app.parent_chunks;
CREATE POLICY published_source ON app.parent_chunks FOR SELECT TO tutoria_api
 USING(auth.published_run_visible(course_id,run_id));
DROP POLICY read_members ON app.child_chunks;
CREATE POLICY published_source ON app.child_chunks FOR SELECT TO tutoria_api
 USING(auth.published_run_visible(course_id,run_id));
DROP POLICY read_members ON app.chunk_embeddings;
CREATE POLICY published_source ON app.chunk_embeddings FOR SELECT TO tutoria_api
 USING(auth.published_run_visible(course_id,run_id));

DROP POLICY scope ON app.generation_sources;
CREATE POLICY own_generation_sources ON app.generation_sources
 USING(auth.has_course_role(course_id,ARRAY['student','coordinator']) AND EXISTS(
  SELECT 1 FROM app.generation_runs g WHERE g.course_id=generation_sources.course_id
    AND g.id=generation_sources.generation_id AND g.user_id=auth.uid()))
 WITH CHECK(auth.has_course_role(course_id,ARRAY['student','coordinator']) AND EXISTS(
  SELECT 1 FROM app.generation_runs g WHERE g.course_id=generation_sources.course_id
    AND g.id=generation_sources.generation_id AND g.user_id=auth.uid()));
