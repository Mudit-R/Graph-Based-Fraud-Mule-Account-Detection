#!/usr/bin/env bash
set -euo pipefail

: "${FRAUD_CASE_NEO4J_PASSWORD:?Render must generate FRAUD_CASE_NEO4J_PASSWORD}"
export NEO4J_AUTH="neo4j/${FRAUD_CASE_NEO4J_PASSWORD}"
export FRAUD_CASE_NEO4J_URI="bolt://127.0.0.1:7687"
export FRAUD_CASE_PUBLIC_DEMO=true
export FRAUD_CASE_BOOTSTRAP_DEMO=true
export FRAUD_CASE_DATABASE=/app/case-data/review.sqlite3

/startup/docker-entrypoint.sh neo4j console &
neo4j_pid=$!
api_pid=""
stop_children() {
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  kill "$neo4j_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap stop_children EXIT INT TERM

ready=0
for attempt in $(seq 1 90); do
  if cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p "$FRAUD_CASE_NEO4J_PASSWORD" "RETURN 1" >/dev/null 2>&1; then
    ready=1
    break
  fi
  kill -0 "$neo4j_pid" 2>/dev/null || { echo "Neo4j exited before becoming ready." >&2; exit 1; }
  sleep 2
done
[[ "$ready" == 1 ]] || { echo "Neo4j readiness timed out." >&2; exit 1; }

"/opt/case-api/bin/python" -m uvicorn src.api.case_review:create_app_from_env \
  --factory --host 0.0.0.0 --port "${PORT:-8080}" &
api_pid=$!
set +e
wait -n "$neo4j_pid" "$api_pid"
status=$?
set -e
exit "$status"
