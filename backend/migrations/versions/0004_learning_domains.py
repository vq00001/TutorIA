"""Course graph and private learning domains. Definitions frozen with this revision."""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    def sql(value):
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(value)

    def table(name, fields, private=False, entity=True, extra=""):
        prefix = ("id uuid PRIMARY KEY," if entity else "") + "course_id uuid NOT NULL,"
        if private:
            prefix += "user_id uuid NOT NULL,"
        constraints = ",FOREIGN KEY(course_id) REFERENCES app.courses ON DELETE RESTRICT"
        if private:
            constraints += (
                ",FOREIGN KEY(course_id,user_id) REFERENCES app.memberships ON DELETE RESTRICT"
            )
        if entity:
            constraints += ",UNIQUE(course_id,id)"
            if private:
                constraints += ",UNIQUE(course_id,user_id,id)"
        sql(f"CREATE TABLE app.{name} ({prefix}{fields}{constraints}{extra})")
        sql(
            f"ALTER TABLE app.{name} ENABLE ROW LEVEL SECURITY; ALTER TABLE app.{name} FORCE ROW LEVEL SECURITY"
        )
        pred = "auth.has_course_role(course_id,ARRAY['student','coordinator'])"
        if private:
            pred += " AND user_id=auth.uid()"
        sql(f"CREATE POLICY scope ON app.{name} USING({pred}) WITH CHECK({pred})")
        sql(f"GRANT SELECT,INSERT,UPDATE,DELETE ON app.{name} TO tutoria_api,tutoria_worker")
        sql(f"CREATE INDEX ON app.{name}(course_id" + (",user_id" if private else "") + ")")
        if "updated_at " in fields:
            sql(
                f"CREATE TRIGGER touch BEFORE UPDATE ON app.{name} FOR EACH ROW EXECUTE FUNCTION app.touch()"
            )

    created = "created_at timestamptz NOT NULL DEFAULT now()"
    updated = "updated_at timestamptz NOT NULL DEFAULT now()"
    mutable = created + "," + updated + ",row_version bigint NOT NULL DEFAULT 1"

    def state(name, values, default):
        allowed = ",".join("'" + v + "'" for v in values.split("/"))
        return f"{name} text NOT NULL DEFAULT '{default}' CHECK({name} IN ({allowed}))"

    table(
        "generation_runs",
        "kind text NOT NULL CHECK(kind IN ('chat','exam','concepts','roadmap_explanation')),"
        + state("status", "queued/running/completed/failed/cancelled/invalidated", "queued")
        + ",provider text NOT NULL,model_name text NOT NULL,model_revision text NOT NULL,prompt_version text NOT NULL,parameters jsonb NOT NULL,"
        + "corpus_revision bigint NOT NULL,graph_revision bigint NOT NULL,request_hash bytea NOT NULL CHECK(octet_length(request_hash)=32),"
        + "input_tokens bigint CHECK(input_tokens>=0),output_tokens bigint CHECK(output_tokens>=0),estimated_cost_usd numeric(14,6),"
        + "reserved_cost_usd numeric(14,6) NOT NULL DEFAULT 0 CHECK(reserved_cost_usd>=0),error_code text,"
        + created
        + ",started_at timestamptz,finished_at timestamptz",
        True,
    )
    table(
        "conversations",
        "title varchar(255) NOT NULL,"
        + state("status", "active/closed/deletion_pending", "active")
        + ","
        + created
        + ","
        + updated,
        True,
    )
    table(
        "messages",
        "conversation_id uuid NOT NULL,sequence_no bigint NOT NULL CHECK(sequence_no>0),"
        + state("role", "user/assistant", "user")
        + ","
        + state("status", "pending/streaming/completed/failed/cancelled/redacted", "pending")
        + ",parts jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(parts)='array' AND jsonb_array_length(parts)<=20 AND octet_length(parts::text)<=262144),"
        + "schema_version int NOT NULL DEFAULT 1,in_reply_to uuid,generation_id uuid,redacted_at timestamptz,"
        + created
        + ","
        + updated,
        True,
        extra=",UNIQUE(conversation_id,sequence_no),UNIQUE(course_id,user_id,conversation_id,id)",
    )
    table(
        "generation_sources",
        "generation_id uuid NOT NULL,parent_id uuid NOT NULL,run_id uuid NOT NULL,rank int NOT NULL CHECK(rank>0),score double precision NOT NULL",
        entity=False,
        extra=",PRIMARY KEY(generation_id,parent_id),UNIQUE(generation_id,rank),UNIQUE(course_id,generation_id,parent_id)",
    )
    table(
        "citations",
        "message_id uuid NOT NULL,generation_id uuid NOT NULL,parent_id uuid,ordinal int NOT NULL CHECK(ordinal>0),label varchar(255) NOT NULL,locator jsonb NOT NULL,"
        + state("status", "available/source_removed", "available"),
        True,
        extra=",UNIQUE(message_id,ordinal)",
    )
    table(
        "generated_artifacts",
        "generation_id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('summary','flashcards','explanation')),title varchar(255) NOT NULL,content jsonb NOT NULL,schema_version int NOT NULL DEFAULT 1,"
        + state("status", "active/invalidated/redacted", "active")
        + ","
        + created,
        True,
    )
    table(
        "concepts",
        "canonical_key varchar(160) NOT NULL,label varchar(255) NOT NULL,description text NOT NULL DEFAULT '',"
        + state("status", "draft/approved/retired", "draft")
        + ",estimated_minutes int NOT NULL DEFAULT 20 CHECK(estimated_minutes BETWEEN 1 AND 240),"
        + ",".join(
            state(c, "manual/llm", "manual")
            for c in ("origin", "label_origin", "description_origin")
        )
        + ",created_by uuid REFERENCES app.users ON DELETE RESTRICT,"
        + mutable,
        extra=",UNIQUE(course_id,canonical_key)",
    )
    table(
        "concept_aliases",
        "concept_id uuid NOT NULL,alias varchar(255) NOT NULL,normalized_alias varchar(255) NOT NULL",
        entity=False,
        extra=",PRIMARY KEY(course_id,concept_id,normalized_alias)",
    )
    table(
        "concept_sources",
        "concept_id uuid NOT NULL,parent_id uuid NOT NULL,run_id uuid NOT NULL,"
        + state("origin", "manual/llm", "manual")
        + ",generation_id uuid",
        entity=False,
        extra=",PRIMARY KEY(concept_id,parent_id)",
    )
    table(
        "concept_edges",
        "source_id uuid NOT NULL,target_id uuid NOT NULL,"
        + state("relation", "prerequisite/part_of/related", "prerequisite")
        + ","
        + state("status", "draft/approved/retired", "draft")
        + ","
        + state("origin", "manual/llm", "manual")
        + ",confidence numeric(8,5) CHECK(confidence BETWEEN 0 AND 1),"
        + mutable,
        extra=",CHECK(source_id<>target_id),CHECK(relation<>'related' OR source_id<target_id),UNIQUE(course_id,source_id,target_id,relation)",
    )
    table(
        "edge_sources",
        "edge_id uuid NOT NULL,parent_id uuid NOT NULL,run_id uuid NOT NULL,generation_id uuid",
        entity=False,
        extra=",PRIMARY KEY(edge_id,parent_id)",
    )
    table(
        "exams",
        "generation_id uuid NOT NULL,title varchar(255) NOT NULL,kind text NOT NULL CHECK(kind IN ('diagnostic','practice')),"
        + state("status", "generating/ready/failed/invalidated", "generating")
        + ",duration_seconds int NOT NULL CHECK(duration_seconds BETWEEN 60 AND 7200),available_at timestamptz,"
        + created
        + ",published_at timestamptz,invalidated_at timestamptz",
        True,
    )
    table(
        "questions",
        "exam_id uuid NOT NULL,concept_id uuid NOT NULL,position int NOT NULL CHECK(position BETWEEN 1 AND 20),"
        + state("kind", "alternatives/multiple/true_false/development", "alternatives")
        + ",prompt text NOT NULL,max_points numeric(8,5) NOT NULL DEFAULT 1 CHECK(max_points>0),"
        + created,
        True,
        extra=",UNIQUE(exam_id,position),UNIQUE(course_id,user_id,exam_id,id)",
    )
    table(
        "question_options",
        "exam_id uuid NOT NULL,question_id uuid NOT NULL,position int NOT NULL CHECK(position>0),text text NOT NULL CHECK(length(btrim(text))>0)",
        True,
        extra=",UNIQUE(question_id,position),UNIQUE(course_id,user_id,exam_id,question_id,id)",
    )
    table(
        "question_keys",
        "question_id uuid PRIMARY KEY,exam_id uuid NOT NULL,expected_answer text,rubric jsonb,explanation text NOT NULL",
        True,
        False,
    )
    table(
        "correct_options",
        "exam_id uuid NOT NULL,question_id uuid NOT NULL,option_id uuid NOT NULL",
        True,
        False,
        ",PRIMARY KEY(question_id,option_id)",
    )
    table(
        "question_sources",
        "question_id uuid NOT NULL,parent_id uuid NOT NULL,run_id uuid NOT NULL",
        True,
        False,
        ",PRIMARY KEY(question_id,parent_id)",
    )
    table(
        "exam_attempts",
        "exam_id uuid NOT NULL,attempt_no int NOT NULL CHECK(attempt_no>0),"
        + state(
            "status", "in_progress/submitted/grading/graded/needs_review/invalidated", "in_progress"
        )
        + ",started_at timestamptz NOT NULL DEFAULT now(),deadline_at timestamptz NOT NULL,submitted_at timestamptz,graded_at timestamptz,score_percent numeric(8,5) CHECK(score_percent BETWEEN 0 AND 100),"
        + created
        + ",row_version bigint NOT NULL DEFAULT 1",
        True,
        extra=",UNIQUE(exam_id,attempt_no),UNIQUE(course_id,user_id,exam_id,id)",
    )
    table(
        "answers",
        "exam_id uuid NOT NULL,attempt_id uuid NOT NULL,question_id uuid NOT NULL,text_answer text,flagged boolean NOT NULL DEFAULT false,"
        + updated
        + ",row_version bigint NOT NULL DEFAULT 1",
        True,
        extra=",UNIQUE(attempt_id,question_id),UNIQUE(course_id,user_id,exam_id,question_id,id)",
    )
    table(
        "answer_selections",
        "exam_id uuid NOT NULL,question_id uuid NOT NULL,answer_id uuid NOT NULL,option_id uuid NOT NULL",
        True,
        False,
        ",PRIMARY KEY(answer_id,option_id)",
    )
    table(
        "grades",
        "answer_id uuid NOT NULL,revision int NOT NULL CHECK(revision>0),points numeric(8,5),"
        + state("status", "accepted/needs_review/invalidated", "needs_review")
        + ","
        + state("method", "deterministic/llm/manual", "deterministic")
        + ",feedback text NOT NULL,grader_version text NOT NULL,generation_id uuid,supersedes_id uuid,"
        + created,
        True,
        extra=",UNIQUE(answer_id,revision)",
    )
    table(
        "mastery_events",
        "concept_id uuid NOT NULL,grade_id uuid NOT NULL,value numeric(8,5) NOT NULL CHECK(value BETWEEN 0 AND 1),weight numeric(8,5) NOT NULL CHECK(weight>0),algorithm_version text NOT NULL DEFAULT 'beta_v1',valid boolean NOT NULL DEFAULT true,"
        + created,
        True,
        extra=",UNIQUE(grade_id,concept_id,algorithm_version)",
    )
    table(
        "concept_mastery",
        "concept_id uuid NOT NULL,alpha numeric(12,5) NOT NULL DEFAULT 1 CHECK(alpha>0),beta numeric(12,5) NOT NULL DEFAULT 1 CHECK(beta>0),evidence_count int NOT NULL DEFAULT 0 CHECK(evidence_count>=0),"
        + state("level", "unknown/reinforce/developing/mastered", "unknown")
        + ",mastery_revision bigint NOT NULL DEFAULT 0,algorithm_version text NOT NULL DEFAULT 'beta_v1',"
        + updated,
        True,
        False,
        ",PRIMARY KEY(course_id,user_id,concept_id)",
    )
    table(
        "learning_profiles",
        "mastery_revision bigint NOT NULL DEFAULT 0," + updated,
        True,
        False,
        ",PRIMARY KEY(course_id,user_id)",
    )
    table(
        "learning_goals",
        "title varchar(255) NOT NULL,target_date date,"
        + state("status", "active/completed/archived", "active")
        + ","
        + mutable,
        True,
    )
    table(
        "goal_concepts",
        "goal_id uuid NOT NULL,concept_id uuid NOT NULL",
        True,
        False,
        ",PRIMARY KEY(goal_id,concept_id)",
    )
    table(
        "roadmaps",
        "goal_id uuid NOT NULL,version_no int NOT NULL CHECK(version_no>0),"
        + state("status", "active/superseded/stale/blocked", "active")
        + ",corpus_revision bigint NOT NULL,graph_revision bigint NOT NULL,mastery_revision bigint NOT NULL,algorithm_version text NOT NULL DEFAULT 'toposort_v1',parameters jsonb NOT NULL,"
        + created,
        True,
        extra=",UNIQUE(goal_id,version_no)",
    )
    table(
        "roadmap_steps",
        "roadmap_id uuid NOT NULL,concept_id uuid NOT NULL,position int NOT NULL CHECK(position>0),reason text NOT NULL,estimated_minutes int NOT NULL CHECK(estimated_minutes BETWEEN 1 AND 240),"
        + state("state", "pending/in_progress/completed/blocked", "pending")
        + ",completed_at timestamptz",
        True,
        extra=",UNIQUE(roadmap_id,position),UNIQUE(roadmap_id,concept_id)",
    )
    table(
        "study_sessions",
        "started_at timestamptz NOT NULL,last_heartbeat_at timestamptz NOT NULL,ended_at timestamptz,active_seconds bigint NOT NULL DEFAULT 0 CHECK(active_seconds>=0),"
        + created,
        True,
    )
    table(
        "study_intervals",
        "session_id uuid NOT NULL,sequence_no bigint NOT NULL,start_at timestamptz NOT NULL,end_at timestamptz NOT NULL,"
        + created,
        True,
        extra=",UNIQUE(session_id,sequence_no),CHECK(end_at>start_at AND end_at-start_at<=interval '30 seconds')",
    )

    def fk(src, cols, dst, target=None):
        sql(
            f"ALTER TABLE app.{src} ADD FOREIGN KEY({cols}) REFERENCES app.{dst}({target or cols}) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"
        )

    for src, col, dst in [
        ("messages", "conversation_id", "conversations"),
        ("messages", "generation_id", "generation_runs"),
        ("citations", "message_id", "messages"),
        ("citations", "generation_id", "generation_runs"),
        ("generated_artifacts", "generation_id", "generation_runs"),
        ("exams", "generation_id", "generation_runs"),
        ("questions", "exam_id", "exams"),
        ("question_sources", "question_id", "questions"),
        ("grades", "answer_id", "answers"),
        ("grades", "generation_id", "generation_runs"),
        ("grades", "supersedes_id", "grades"),
        ("mastery_events", "grade_id", "grades"),
        ("goal_concepts", "goal_id", "learning_goals"),
        ("roadmaps", "goal_id", "learning_goals"),
        ("roadmap_steps", "roadmap_id", "roadmaps"),
        ("study_intervals", "session_id", "study_sessions"),
    ]:
        fk(src, "course_id,user_id," + col, dst, "course_id,user_id,id")
    fk(
        "messages",
        "course_id,user_id,conversation_id,in_reply_to",
        "messages",
        "course_id,user_id,conversation_id,id",
    )
    for src, col, dst in [
        ("concept_aliases", "concept_id", "concepts"),
        ("concept_sources", "concept_id", "concepts"),
        ("concept_edges", "source_id", "concepts"),
        ("concept_edges", "target_id", "concepts"),
        ("edge_sources", "edge_id", "concept_edges"),
        ("questions", "concept_id", "concepts"),
        ("mastery_events", "concept_id", "concepts"),
        ("concept_mastery", "concept_id", "concepts"),
        ("goal_concepts", "concept_id", "concepts"),
        ("roadmap_steps", "concept_id", "concepts"),
        ("generation_sources", "generation_id", "generation_runs"),
        ("concept_sources", "generation_id", "generation_runs"),
        ("edge_sources", "generation_id", "generation_runs"),
    ]:
        fk(src, "course_id," + col, dst, "course_id,id")
    for src in ("generation_sources", "concept_sources", "edge_sources", "question_sources"):
        fk(src, "course_id,run_id,parent_id", "parent_chunks", "course_id,run_id,id")
    fk("citations", "course_id,generation_id,parent_id", "generation_sources")
    for src in ("question_options", "question_keys"):
        fk(
            src,
            "course_id,user_id,exam_id,question_id",
            "questions",
            "course_id,user_id,exam_id,id",
        )
    fk("exam_attempts", "course_id,user_id,exam_id", "exams", "course_id,user_id,id")
    fk(
        "answers",
        "course_id,user_id,exam_id,attempt_id",
        "exam_attempts",
        "course_id,user_id,exam_id,id",
    )
    fk(
        "answers",
        "course_id,user_id,exam_id,question_id",
        "questions",
        "course_id,user_id,exam_id,id",
    )
    for src in ("correct_options", "answer_selections"):
        fk(
            src,
            "course_id,user_id,exam_id,question_id,option_id",
            "question_options",
            "course_id,user_id,exam_id,question_id,id",
        )
    fk(
        "answer_selections",
        "course_id,user_id,exam_id,question_id,answer_id",
        "answers",
        "course_id,user_id,exam_id,question_id,id",
    )
    sql(
        "CREATE UNIQUE INDEX ON app.messages(conversation_id) WHERE role='assistant' AND status IN ('pending','streaming')"
    )
    sql("CREATE UNIQUE INDEX ON app.exam_attempts(user_id,exam_id) WHERE status='in_progress'")
    sql("CREATE UNIQUE INDEX ON app.roadmaps(goal_id) WHERE status='active'")
    sql("CREATE UNIQUE INDEX ON app.study_sessions(user_id) WHERE ended_at IS NULL")
    sql(
        "CREATE INDEX ON app.concept_edges(course_id,relation,status,source_id,target_id); CREATE INDEX ON app.concept_edges(course_id,target_id)"
    )
    for t in ("generation_sources", "concept_sources", "edge_sources", "question_sources"):
        sql(f"CREATE INDEX ON app.{t}(parent_id)")
    sql("REVOKE ALL ON app.question_keys,app.correct_options FROM tutoria_api")
    sql(
        "GRANT SELECT,INSERT,UPDATE,DELETE ON app.question_keys,app.correct_options TO tutoria_worker"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
