"""Integrity triggers must inspect aggregates beyond an actor's visible rows."""

from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql("""
    GRANT SELECT ON public.alembic_version TO tutoria_api,tutoria_worker;
    ALTER FUNCTION app.require_coordinator() SECURITY DEFINER;
    ALTER FUNCTION app.require_coordinator() OWNER TO tutoria_security;
    ALTER FUNCTION app.graph_guard() SECURITY DEFINER;
    ALTER FUNCTION app.graph_guard() OWNER TO tutoria_security;
    ALTER FUNCTION app.concept_revision() SECURITY DEFINER;
    ALTER FUNCTION app.concept_revision() OWNER TO tutoria_security;
    CREATE FUNCTION auth.lock_course(cid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF NOT auth.has_course_role(cid,ARRAY['student','coordinator']) THEN
        RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
      PERFORM 1 FROM app.courses WHERE id=cid FOR UPDATE;
    END $$;
    ALTER FUNCTION auth.lock_course(uuid) OWNER TO tutoria_security;
    REVOKE ALL ON FUNCTION auth.lock_course(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION auth.lock_course(uuid) TO tutoria_api,tutoria_worker;
    ALTER FUNCTION app.check_duplicate() SECURITY DEFINER;
    ALTER FUNCTION app.check_duplicate() OWNER TO tutoria_security;
    CREATE OR REPLACE FUNCTION app.check_duplicate() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF NOT auth.has_course_role(NEW.course_id,ARRAY['student','coordinator']) THEN
        RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE='42501'; END IF;
      PERFORM 1 FROM app.courses WHERE id=NEW.course_id FOR UPDATE;
      IF EXISTS(SELECT FROM app.document_versions WHERE course_id=NEW.course_id AND sha256=NEW.sha256 AND status<>'purged' AND id<>NEW.id)
      THEN RAISE EXCEPTION 'DUPLICATE_DOCUMENT' USING ERRCODE='23505'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION app.require_coordinator(),app.graph_guard(),app.concept_revision(),app.check_duplicate() FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
