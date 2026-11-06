#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=api_password="$API_DB_PASSWORD" --set=worker_password="$WORKER_DB_PASSWORD" <<'SQL'
CREATE ROLE tutoria_api LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'api_password';
CREATE ROLE tutoria_worker LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'worker_password';
SQL
