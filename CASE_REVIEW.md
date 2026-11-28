# Fraud case review

The case-review service ingests batches of synthetic payment events, uses the
existing ring detector to group linked transactions and saves the evidence graph
alongside a persistent analyst queue. Optional account risk scores can come from
the existing scoring pipeline. A graph connection or score is a review lead.
The service records escalation or dismissal decisions for a person to act on.

## Run locally

Install the small API dependency set from the repository root:

    python -m pip install -r requirements-case-review.txt

Set FRAUD_CASE_ANALYST to a local operator name and FRAUD_CASE_TOKEN to a random
ASCII secret of at least 32 characters. You can generate a token with:

    python -c "import secrets; print(secrets.token_urlsafe(32))"

Set FRAUD_CASE_DATABASE to choose the database file. Its default is
case-data/review.sqlite3. Start the service from the repository root:

    python -m uvicorn src.api.case_review:create_app_from_env --factory --host 127.0.0.1 --port 8001

Open http://127.0.0.1:8001/analyst/ and connect with the configured analyst
token. The console keeps the token in memory for the current browser tab.
Choose **Load synthetic case**, select a payment or identifier to explore its
connections, and record a review. The console shows the resulting audit event and
can reopen the case after restarting the service. An admin or intake operator
can import the same JSON format directly from the console.

The OpenAPI interface is at http://127.0.0.1:8001/docs. To exercise the same
flow by API, submit examples/case-review-batch.json to POST /batches.
GET /cases/{case_id}/graph returns a typed subgraph bounded by depth and node
count. To set an anchor, specify anchor_type and anchor_id together. Start with
the returned case version when posting to /cases/{case_id}/reviews.

## Analyst roles

For multiple operators, set **FRAUD_CASE_CREDENTIALS_JSON** to an object whose
keys are analyst IDs. Each value contains **role** and **token** fields, for
example **{"case-reviewer":{"role":"reviewer","token":"<generated-secret>"}}**.
Generate a distinct token for each operator. The registry takes precedence
when configured. The single-account launch described above grants admin access.

| Role | Access |
| --- | --- |
| viewer | Read cases, evidence and review history |
| reviewer | Viewer access and permission to record review decisions |
| ingest | Import batches and load the synthetic intake example |
| admin | Read, review and import |

The API verifies permissions on each operation. Review records identify the
authenticated analyst. Restart the service after changing the credential registry.

## High-risk ring webhooks

The case API can send a signed, redacted alert when a newly detected ring's
mean account fraud score meets a configured threshold. Delivery is disabled when
`FRAUD_CASE_ALERT_WEBHOOK_URL` is unset. To enable it, configure an HTTPS target
and a unique signing secret of at least 32 ASCII characters:

```powershell
$env:FRAUD_CASE_ALERT_WEBHOOK_URL = 'https://your-owned-endpoint.example/fraud-alerts'
$env:FRAUD_CASE_ALERT_WEBHOOK_SECRET = 'replace-with-a-random-secret-of-32-or-more-characters'
$env:FRAUD_CASE_ALERT_THRESHOLD = '0.70'
```

The threshold defaults to `0.70`. The event contains a case ID, score, risk
level, typology, event counts, exposure and detection time. It omits customer,
device, IP and account identifiers. Its text field makes the JSON payload compatible with Slack incoming webhooks and general HTTP receivers. The request carries an HMAC-SHA256 signature
in `X-Case-Signature` over the exact UTF-8 request body, plus a stable
`Idempotency-Key` and `X-Case-Event-Id`. Verify the signature before acting and
deduplicate on the event ID. Use the configured secret only in the service and
the destination's secret manager; never put it in the repository.

Case creation and alert enqueue share one SQLite transaction. The lifespan
worker retries delivery with exponential backoff (six attempts, capped at five
minutes). Failures remain in the local outbox as pending or dead-letter records;
successful events are marked delivered. There is no delivery when the URL is
unset, and deployment must keep the case database durable if pending alerts need
to survive a restart. The public free demo's ephemeral storage is not suitable
for durable alert delivery.

Administrators can call `GET /ops/webhook-delivery` with their bearer token
to see counts of pending, sending, delivered and dead alerts, plus the creation
time of the oldest outstanding alert as Unix seconds. Workers claim one alert
at a time. An expired claim can be retried, and only the current attempt can
update delivery state. Receivers should still deduplicate by event ID.

## Neo4j graph load test

With the graph-profile API running and the synthetic cross-case fixture loaded,
run the k6 script from the repository root:

```powershell
$env:BASE_URL = 'http://127.0.0.1:8002'
$env:FRAUD_CASE_TOKEN = '<reviewer-or-admin-bearer-token>'
k6 run --summary-trend-stats='avg,min,med,max,p(95),p(99)' loadtest/case-graph.js
```

It performs 30 sequential warm requests to the authenticated four-hop
`/related-graph` endpoint, resolving a case through `/cases` unless `CASE_ID` is
provided. k6 reports `med` (p50) and p95 latency, request failures, and response
checks. The original 59.51 ms p50 / 75.84 ms p95 figures were measured locally
on a synthetic 240-case, 1,440-payment Neo4j fixture with 30 warm requests.
Reproduce a comparable sample by using the same fixture, local machine, Neo4j
version and query limits. Results from different hardware, graph sizes, cold
projection state or concurrent load are not directly comparable; this script
measures the current setup and does not force those historical numbers.

## Container

To start a container with a local persistent volume, export the same variables
and run:

    docker compose -f compose.case-review.yml up --build

This starts the SQLite version at **http://127.0.0.1:8001/analyst/**.

### Neo4j cross-case graph

Use the graph profile to start an additional API variant with an authenticated
Neo4j Community 5.26 database. Generate a password once for the data volume
and keep it in your local environment. In PowerShell:

    $env:FRAUD_CASE_NEO4J_PASSWORD = (python -c "import secrets; print(secrets.token_urlsafe(32))")
    docker compose -f compose.case-review.yml --profile graph up --build case-review-neo4j

Open **http://127.0.0.1:8002/analyst/**. Use **Load related cases** to import the
paired example and explore cross-case payment links from a shared identifier.
The API connects to Neo4j on the private Compose network and waits for an
authenticated database health check. Host access to Bolt is loopback-only at
port **17687** and can be changed via **FRAUD_CASE_NEO4J_BOLT_PORT**.
Neo4j and the graph variant's SQLite file use separate persistent volumes.
If restarting with the same volumes, reuse the password used at initialization.

For a locally started API, set **FRAUD_CASE_NEO4J_URI** to a Bolt connection such
as **bolt://127.0.0.1:17687**, with **FRAUD_CASE_NEO4J_PASSWORD** and optional
**FRAUD_CASE_NEO4J_USER** and **FRAUD_CASE_NEO4J_DATABASE**. Both optional values
default to **neo4j**. Leave the URI unset for case-local SQLite graph traversal.

To run the opt-in database integration tests against an isolated development
Neo4j instance in PowerShell:

    $env:FRAUD_TEST_NEO4J_URI = 'bolt://127.0.0.1:17687'
    $env:FRAUD_TEST_NEO4J_PASSWORD = $env:FRAUD_CASE_NEO4J_PASSWORD
    python -m pytest -q tests/test_case_neo4j.py

Set **FRAUD_TEST_NEO4J_USER** and **FRAUD_TEST_NEO4J_DATABASE** if the instance
uses different values. The tests use their own graph workspaces.

## Storage and workflow

- Graph edges distinguish accounts, merchants, devices, IP identifiers, addresses,
  promotions and bank accounts. They link back to the specific ingested payment.
- A batch ID and normalized payload digest make replay safe within that batch.
  Reusing an ID with changed data yields HTTP 409.
- SQLite persists intake, payment evidence and generated cases in one transaction.
  A version check serializes competing analyst decisions. The state change and
  review event commit together.
- List cases by status and paginate with the last case ID from the previous page.
  Case detail returns only the evidence linked to the selected case.
- SQLite is the source of truth for analyst reviews and case intake. The optional
  Neo4j projection incrementally replays case evidence to support cross-case
  traversal. Local single-case review can continue when that projection is offline.

Run the API and detector regression tests using the project's development
dependencies:

    python -m pytest -q tests/test_case_review.py tests/test_ring_identifier_links.py

See [Point-in-time PaySim evaluation](PAYSIM_EVALUATION.md) for the separate fraud-model and ring-traversal benchmarks.

See [cross-case investigation verification](CROSS_CASE_EVALUATION.md) for the browser check and local Neo4j benchmark.

### Public synthetic demo

The optional Render demo runs the console and Neo4j in one container and can
be opened at `/analyst/?demo=1`. Set `FRAUD_CASE_PUBLIC_DEMO=true` to allow
unauthenticated viewer access and `FRAUD_CASE_BOOTSTRAP_DEMO=true` to load the
bundled synthetic linked batches on service startup. Public access is read-only:
the viewer cannot import payments or change case decisions. Use synthetic demo
records only. The free service uses ephemeral storage, so its sample cases and
Neo4j projection are rebuilt when the container restarts.
