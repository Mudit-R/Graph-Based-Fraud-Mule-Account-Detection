# Cross-case investigation verification

The console provides a Connected cases view backed by Neo4j. Start from a
payment or typed identifier, explore up to four hops, and inspect the
shortest visible evidence path into each linked case. A linked payment or
case opens its SQLite-backed review and decision history.

## End-to-end check

The local tests used Neo4j Community **5.26.34** and four synthetic batches:

- East and West share a device.
- West and South share a beneficiary bank account.
- A control batch carries the shared device's text as an IP identifier.
  The control stays outside the device investigation.

A headless Chromium browser loaded the demo, found **two related cases from
the East seed**, switched between the current-case and Neo4j graph, opened a
West payment with the same raw ID as an East payment, and recorded a decision
in South. A viewer could explore the evidence on mobile with read-only access.
API tests also covered backfill, repeat intake, workspace isolation,
transactional projection rollback, and persisted review history.

## Real Neo4j load check

The test created **240 case snapshots and 1,440 synthetic payments** in an
isolated SQLite workspace. A four-hop investigation returned the **12 expected
cases including the seed**, with 200 visible nodes and 247 visible edges.
The node cap was reached and the API reported truncation.

| Measurement | Result |
| --- | ---: |
| Cold projection and search | 2.35 seconds across three bounded requests |
| Warm local API p50 | 59.51 ms |
| Warm local API p95 | 75.84 ms |
| Repeat warm requests | 30, all returned identical evidence |

The request times include FastAPI handling, SQLite anchor validation,
projection cursor checks, and Neo4j queries. The timing fixture ran on a
local machine with a synthetic graph. See [raw benchmark output](outputs/neo4j_cross_case_benchmark.json).

## Reproduce

Use a dedicated Neo4j test instance. Export FRAUD_TEST_NEO4J_URI,
FRAUD_TEST_NEO4J_PASSWORD and, if needed, FRAUD_TEST_NEO4J_USER
and FRAUD_TEST_NEO4J_DATABASE. The test commands write generated case
graphs to that instance under isolated workspace IDs.

    python -m pytest -q tests/test_case_review.py tests/test_case_graph_projection.py tests/test_case_neo4j.py tests/test_neo4j_cross_case_integration.py tests/test_ring_identifier_links.py
    node --test tests/test_cross_case_paths.mjs
    python -m scripts.benchmark_neo4j_case_graph --output outputs/neo4j_cross_case_benchmark.json

For the browser check, install Node.js and Playwright in a development
environment, then make the Playwright module available to Node. Set
PLAYWRIGHT_MODULE if the package is outside normal module resolution.
Install Playwright's Chromium or set CASE_SMOKE_CHROME to an installed Chrome
executable.

    python -m scripts.smoke_neo4j_console

See [case-review setup](CASE_REVIEW.md) for the console and database. The
[temporal GNN evaluation](TEMPORAL_GNN.md) remains at **95.18% held-out
ROC-AUC** on PaySim. The graph projection does not update model artifacts.
