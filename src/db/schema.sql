CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE runs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_date date NOT NULL UNIQUE, status text NOT NULL, target_count integer NOT NULL DEFAULT 12 CHECK(target_count BETWEEN 1 AND 12), started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, metrics jsonb NOT NULL DEFAULT '{}');
CREATE TABLE sources (id text PRIMARY KEY, run_id uuid REFERENCES runs(id), source_url text NOT NULL, platform text NOT NULL, title text NOT NULL, published_at timestamptz, fetched_at timestamptz NOT NULL DEFAULT now(), collection_method text, metrics jsonb NOT NULL DEFAULT '{}', evidence jsonb NOT NULL DEFAULT '{}', rights_state text NOT NULL DEFAULT 'UNVERIFIED');
CREATE TABLE stories (id text PRIMARY KEY, source_id text REFERENCES sources(id), status text NOT NULL, approved_version integer, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source_id, id));
CREATE TABLE story_versions (story_id text NOT NULL REFERENCES stories(id), version integer NOT NULL, payload jsonb NOT NULL, qa jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(story_id,version));
CREATE TABLE generation_jobs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_id uuid REFERENCES runs(id), story_id text REFERENCES stories(id), stage text NOT NULL, status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','needs_review')), input_sha256 text NOT NULL, idempotency_key text NOT NULL UNIQUE, attempt integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 3, next_retry_at timestamptz, provider_name text, provider_version text, request jsonb NOT NULL, result jsonb, error jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE assets (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), story_id text REFERENCES stories(id), job_id uuid REFERENCES generation_jobs(id), asset_kind text NOT NULL, entity_id text, version integer NOT NULL DEFAULT 1, variant integer NOT NULL DEFAULT 1, drive_file_id text, drive_parent_id text, sha256 text, size_bytes bigint, mime_type text, prompt_version text, skill_revision text, qa_status text NOT NULL DEFAULT 'PENDING', metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(story_id, asset_kind, entity_id, version, variant));
CREATE TABLE qa_checks (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), story_id text REFERENCES stories(id), job_id uuid REFERENCES generation_jobs(id), check_name text NOT NULL, status text NOT NULL, evidence jsonb NOT NULL DEFAULT '{}', checked_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE approvals (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), story_id text REFERENCES stories(id), stage text NOT NULL, decision text NOT NULL, reviewer text NOT NULL, evidence jsonb NOT NULL DEFAULT '{}', decided_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE drive_folders (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), canonical_path text NOT NULL UNIQUE, parent_drive_id text, drive_folder_id text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE provider_versions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), provider text NOT NULL, upstream_url text, revision text NOT NULL, verified boolean NOT NULL DEFAULT false, smoke_evidence jsonb NOT NULL DEFAULT '{}', activated_at timestamptz, UNIQUE(provider,revision));
CREATE TABLE outbox (id bigserial PRIMARY KEY, event_type text NOT NULL, aggregate_id text NOT NULL, payload jsonb NOT NULL, dedup_key text NOT NULL UNIQUE, status text NOT NULL DEFAULT 'PENDING', attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX generation_jobs_work ON generation_jobs(status,next_retry_at);
CREATE INDEX outbox_pending ON outbox(status,next_attempt_at);
CREATE TABLE job_events (id bigserial PRIMARY KEY, job_id uuid NOT NULL REFERENCES generation_jobs(id), event_key text NOT NULL UNIQUE, from_status text, to_status text NOT NULL, payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE callback_receipts (event_id text PRIMARY KEY, received_at timestamptz NOT NULL DEFAULT now(), payload_sha256 text NOT NULL);
CREATE TABLE budgets (budget_date date NOT NULL, budget_key text NOT NULL, limit_amount numeric(12,4) NOT NULL, reserved_amount numeric(12,4) NOT NULL DEFAULT 0, actual_amount numeric(12,4) NOT NULL DEFAULT 0, PRIMARY KEY(budget_date,budget_key));
CREATE TABLE sheet_sync_queue (id bigserial PRIMARY KEY, entity_type text NOT NULL, entity_id text NOT NULL, payload jsonb NOT NULL, status text NOT NULL DEFAULT 'PENDING', attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(), UNIQUE(entity_type,entity_id));
CREATE TABLE provider_call_intents (job_id uuid NOT NULL REFERENCES generation_jobs(id), provider text NOT NULL, request_sha256 text NOT NULL, status text NOT NULL CHECK(status IN ('IN_PROGRESS','SUCCEEDED','UNCERTAIN')), result jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(job_id,provider));
CREATE INDEX provider_call_intents_state ON provider_call_intents(status,updated_at);
CREATE TABLE provider_budget_reservations (job_id uuid NOT NULL REFERENCES generation_jobs(id), provider text NOT NULL, run_id uuid NOT NULL REFERENCES runs(id), usage_date date NOT NULL, usage_month text NOT NULL, units integer NOT NULL CHECK(units>0), request_count integer NOT NULL DEFAULT 1, reserved_cost numeric(12,4) NOT NULL CHECK(reserved_cost>=0), status text NOT NULL DEFAULT 'RESERVED', created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(job_id,provider));
CREATE INDEX provider_budget_daily ON provider_budget_reservations(provider,usage_date,status);
CREATE INDEX provider_budget_monthly ON provider_budget_reservations(provider,usage_month,status);
-- Fresh installations include signed approval replay storage (migration002 for old schemas).
CREATE TABLE review_callback_events (
    event_id text PRIMARY KEY CHECK (event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
    nonce text NOT NULL UNIQUE CHECK (nonce ~ '^[A-Za-z0-9_-]{16,128}$'),
    review_id text NOT NULL CHECK (length(review_id) BETWEEN 1 AND 200),
    decision text NOT NULL CHECK (decision IN ('approve', 'reject', 'revise')),
    signature_timestamp bigint NOT NULL CHECK (signature_timestamp >= 0),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX review_callback_events_received_at ON review_callback_events (received_at);
