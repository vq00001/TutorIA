"""Separate shared reads, owner writes and worker publication privileges."""

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade():
    bind = op.get_bind().execution_options(no_parameters=True)
    bind.exec_driver_sql("""
      CREATE POLICY extraction_drafts ON app.concepts FOR INSERT TO tutoria_worker
      WITH CHECK(status='draft' AND origin='llm' AND auth.has_course_role(course_id,ARRAY['student','coordinator']));
      CREATE POLICY extraction_sources ON app.concept_sources FOR INSERT TO tutoria_worker
      WITH CHECK(origin='llm' AND auth.has_course_role(course_id,ARRAY['student','coordinator']));
      CREATE POLICY worker_revision ON app.courses FOR UPDATE TO tutoria_worker
      USING(auth.has_course_role(id,ARRAY['student','coordinator']))
      WITH CHECK(auth.has_course_role(id,ARRAY['student','coordinator']));
      DROP POLICY members ON app.documents;
      CREATE POLICY document_read ON app.documents FOR SELECT USING(auth.has_course_role(course_id,ARRAY['student','coordinator']));
      CREATE POLICY document_insert ON app.documents FOR INSERT TO tutoria_api
      WITH CHECK(uploaded_by=auth.uid() AND auth.has_course_role(course_id,ARRAY['student','coordinator']));
      CREATE POLICY document_owner ON app.documents FOR UPDATE TO tutoria_api
      USING(uploaded_by=auth.uid() OR auth.has_course_role(course_id,ARRAY['coordinator']))
      WITH CHECK(uploaded_by=auth.uid() OR auth.has_course_role(course_id,ARRAY['coordinator']));
      CREATE POLICY document_worker ON app.documents TO tutoria_worker
      USING(auth.has_course_role(course_id,ARRAY['student','coordinator']))
      WITH CHECK(auth.has_course_role(course_id,ARRAY['student','coordinator']));
      REVOKE DELETE ON app.documents FROM tutoria_api;
    """)
    for table in (
        "document_versions",
        "file_contents",
        "processing_runs",
        "parent_chunks",
        "child_chunks",
        "chunk_embeddings",
    ):
        bind.exec_driver_sql(f"""
          DROP POLICY members ON app.{table};
          CREATE POLICY read_members ON app.{table} FOR SELECT USING(auth.has_course_role(course_id,ARRAY['student','coordinator']));
          CREATE POLICY worker_write ON app.{table} TO tutoria_worker
          USING(auth.has_course_role(course_id,ARRAY['student','coordinator']))
          WITH CHECK(auth.has_course_role(course_id,ARRAY['student','coordinator']));
        """)
        if table in ("document_versions", "file_contents", "processing_runs"):
            bind.exec_driver_sql(f"""CREATE POLICY upload_owner ON app.{table} FOR INSERT TO tutoria_api
              WITH CHECK(EXISTS(SELECT FROM app.documents d WHERE d.id=document_id AND d.course_id=course_id
                AND (d.uploaded_by=auth.uid() OR auth.has_course_role(d.course_id,ARRAY['coordinator']))));""")
        else:
            bind.exec_driver_sql(f"REVOKE INSERT,UPDATE,DELETE ON app.{table} FROM tutoria_api")


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
