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
