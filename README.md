# Abuse-Ring Sentinel

An end-to-end fraud research and analyst-investigation project. It pairs a
chronological temporal payment GNN with a review console that traces evidence
through payment graphs and across independently ingested case batches.

## What it does

### Temporal payment risk model

`TemporalPaymentGNN` encodes each payment and pools the sender's and
beneficiary's earlier transaction histories with separate four-head attention.
Each endpoint contributes a bounded history from a 24-step window, with
direction and event age attached to each message. The current payment is scored
before its step enters history, preventing same-step look-ahead. Account-balance
columns and simulator fraud flags are excluded from model inputs.

On a **chronological held-out split of 1,248,736 PaySim payments**, the saved
checkpoint achieved **95.18% ROC-AUC** and placed **96 labeled frauds in the top
100 scores**. A target-payment-only neural baseline scored **94.55% ROC-AUC**.
The dataset is simulated, and the graph model's average precision and fixed
review-budget precision were lower than that baseline on this run. These are
offline benchmark results, not production fraud results.

| Held-out measure | Target-only baseline | Temporal GNN |
| --- | ---: | ---: |
| ROC-AUC | 94.55% | **95.18%** |
| Average precision | **15.90%** | 13.18% |
| F1 at validation-selected threshold | 7.61% | **14.72%** |
| Fraud labels among top 100 | 100 | 96 |

The evaluation reads 6.36 million source rows, trains on a sampled chronological
training pool, selects a checkpoint and threshold on validation data, then
scores the untouched test window at its original class distribution. Full split
sizes, replay instructions and raw metrics are in [TEMPORAL_GNN.md](TEMPORAL_GNN.md).

### Cross-case fraud investigation

The FastAPI application ingests payment batches, groups linked transactions into
review cases, and persists evidence and analyst decisions in SQLite. Neo4j is an
optional graph projection for cross-batch investigation. Typed nodes keep a
device identifier distinct from an account or IP value with the same text.
Workspace-scoped projection cursors, bounded backfill pages and idempotent case
snapshots keep graph sync replayable from the SQLite source of truth.

The console supports four-hop searches with a 200-node response cap, clickable
cross-case pivots, review history and viewer/reviewer/ingest/admin capabilities.
The public demo mode is read-only and loads synthetic data only. Start the local
Neo4j-backed version with the `graph` Compose profile; see [CASE_REVIEW.md](CASE_REVIEW.md).

In the local synthetic end-to-end check, Neo4j returned the expected linked
cases through shared device and beneficiary-bank evidence while excluding a
type-mismatched control. A 240-case fixture produced a bounded 12-case result.
Across 30 warm local requests, cross-case search measured **59.51 ms p50** and
**75.84 ms p95**. These timings are local synthetic-fixture measurements, not
hosted service guarantees. Full setup, browser coverage and benchmark details:
[CROSS_CASE_EVALUATION.md](CROSS_CASE_EVALUATION.md).

## Architecture

```text
PaySim chronological history ──> TemporalPaymentGNN ──> held-out score report

Payment batches ──> FastAPI intake ──> SQLite cases / evidence / audit history
                                          │
                                          └──> Neo4j evidence projection
                                                   │
Analyst browser console <── FastAPI case API <─────┘
```

## Run and reproduce

### Case-review console

Install Docker Compose, create local analyst and Neo4j credentials, then start
the graph profile from this repository root:

```powershell
$env:FRAUD_CASE_ANALYST = "local-analyst"
$env:FRAUD_CASE_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:FRAUD_CASE_NEO4J_PASSWORD = (python -c "import secrets; print(secrets.token_urlsafe(32))")
docker compose -f compose.case-review.yml --profile graph up --build case-review-neo4j
```

Open `http://127.0.0.1:8002/analyst/`. The application exposes `/health` and
FastAPI docs at `/docs`. Local persistence, roles, API routes and environment
settings are documented in [CASE_REVIEW.md](CASE_REVIEW.md).

### Temporal GNN

Download the PaySim CSV from the linked dataset source and place it at:
`data/raw/PS_20174392719_1491204439457_log.csv`. Install the project's ML
dependencies, then reproduce training and checkpoint replay:

```powershell
python -m scripts.train_temporal_payment_gnn data/raw/PS_20174392719_1491204439457_log.csv --device cpu --report outputs/temporal_payment_gnn_metrics.json
python -m scripts.replay_temporal_payment_gnn data/raw/PS_20174392719_1491204439457_log.csv outputs/temporal_payment_gnn/temporal_gnn.pt outputs/temporal_payment_gnn_metrics.json
```

The raw data and trained checkpoints are not committed. The metrics artifact
contains the source-data checksum and measured split outcomes. The source data
describes a [synthetic mobile-money simulator](https://www.inderscience.com/info/inarticle.php?artid=93756).

## Verification

The repository includes unit, API, Neo4j integration, retrieval, temporal-data,
and browser-path checks. The Neo4j integration suite uses an isolated graph
workspace. Follow the exact commands in the linked evaluation documents.

## Project reports

- [Temporal GNN design, metrics and replay](TEMPORAL_GNN.md)
- [Neo4j cross-case console evaluation](CROSS_CASE_EVALUATION.md)
- [Local API, roles and container guide](CASE_REVIEW.md)
- [PaySim and ring-traversal evaluation](PAYSIM_EVALUATION.md)
