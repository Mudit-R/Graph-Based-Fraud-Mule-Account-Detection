"""Persistent storage for the analyst case workflow and its transaction graph."""
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from itertools import groupby
from pathlib import Path
import sqlite3
from uuid import uuid4

from src.graph.ring_detector import RingDetectionEngine


class Conflict(ValueError):
    """A retry or concurrent update conflicts with persisted state."""


class CaseStore:
    GRAPH_FIELDS = (
        "customer_id", "merchant_id", "device_id", "ip_id", "address_id",
        "promo_id", "bank_account_id",
    )
    TRANSITIONS = {
        "open": {"in_review"},
        "in_review": {"escalated", "dismissed"},
        "escalated": set(),
        "dismissed": set(),
    }

    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS intake_batches (
                    batch_id TEXT PRIMARY KEY, digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payment_events (
                    batch_id TEXT NOT NULL REFERENCES intake_batches(batch_id),
                    event_id TEXT NOT NULL, amount REAL NOT NULL, event_time REAL NOT NULL,
                    PRIMARY KEY (batch_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS graph_edges (
                    batch_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                    PRIMARY KEY (batch_id, event_id, kind, entity_id),
                    FOREIGN KEY (batch_id, event_id) REFERENCES payment_events(batch_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS review_cases (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES intake_batches(batch_id),
                    risk TEXT NOT NULL, typology TEXT NOT NULL, exposure REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'open', version INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS review_case_queue ON review_cases(state, id);
                CREATE INDEX IF NOT EXISTS review_case_batch ON review_cases(batch_id);
                CREATE TABLE IF NOT EXISTS case_evidence (
                    case_id TEXT NOT NULL REFERENCES review_cases(id),
                    batch_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    PRIMARY KEY (case_id, event_id),
                    FOREIGN KEY (batch_id, event_id) REFERENCES payment_events(batch_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS review_history (
                    case_id TEXT NOT NULL REFERENCES review_cases(id),
                    version INTEGER NOT NULL, analyst_id TEXT NOT NULL,
                    from_state TEXT NOT NULL, to_state TEXT NOT NULL, reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY (case_id, version)
                );
                CREATE TABLE IF NOT EXISTS graph_workspace (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    workspace_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS case_projection (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL UNIQUE REFERENCES review_cases(id)
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO graph_workspace (singleton, workspace_id) VALUES (1, ?)",
                (str(uuid4()),),
            )
            # Migrate existing SQLite workspaces before accepting new intake.
            db.execute(
                """INSERT INTO case_projection (case_id)
                   SELECT c.id FROM review_cases c
                   WHERE NOT EXISTS (SELECT 1 FROM case_projection p WHERE p.case_id = c.id)
                   ORDER BY c.rowid"""
            )
            self.workspace_id = db.execute(
                "SELECT workspace_id FROM graph_workspace WHERE singleton = 1"
            ).fetchone()["workspace_id"]

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, batch_id, transactions, risk_scores):
        ordered = sorted(transactions, key=lambda event: event["transaction_id"])
        payload = json.dumps([ordered, risk_scores], sort_keys=True, separators=(",", ":"))
        digest = sha256(payload.encode()).hexdigest()
        rings = RingDetectionEngine().extract_rings_from_transactions(ordered, risk_scores)

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            batch = db.execute(
                "SELECT digest FROM intake_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if batch:
                if batch["digest"] != digest:
                    raise Conflict("The batch ID has already been used with different input.")
                case_ids = db.execute(
                    "SELECT id FROM review_cases WHERE batch_id = ? ORDER BY id", (batch_id,)
                )
                return {"created": False, "case_ids": [row["id"] for row in case_ids]}

            db.execute("INSERT INTO intake_batches VALUES (?, ?)", (batch_id, digest))
            db.executemany(
                "INSERT INTO payment_events VALUES (?, ?, ?, ?)",
                [(batch_id, event["transaction_id"], event["amount"], event["timestamp"])
                 for event in ordered],
            )
            db.executemany(
                "INSERT INTO graph_edges VALUES (?, ?, ?, ?)",
                [(batch_id, event["transaction_id"], kind, event[kind])
                 for event in ordered for kind in self.GRAPH_FIELDS if event.get(kind)],
            )
            case_ids = []
            for ring in rings:
                identity = json.dumps([batch_id, ring.transaction_ids], separators=(",", ":"))
                case_id = sha256(identity.encode()).hexdigest()
                db.execute(
                    """INSERT INTO review_cases (id, batch_id, risk, typology, exposure)
                       VALUES (?, ?, ?, ?, ?)""",
                    (case_id, batch_id, ring.risk_level, ring.predicted_ring_type,
                     ring.total_exposure_inr),
                )
                db.executemany(
                    "INSERT INTO case_evidence VALUES (?, ?, ?)",
                    [(case_id, batch_id, event_id) for event_id in ring.transaction_ids],
                )
                db.execute("INSERT INTO case_projection (case_id) VALUES (?)", (case_id,))
                case_ids.append(case_id)
            return {"created": True, "case_ids": sorted(case_ids)}

    def latest_projection_seq(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(seq), 0) FROM case_projection").fetchone()[0]

    def projection_page(self, after: int, through: int, limit: int) -> list[dict]:
        """Read one page of source cases and their typed evidence in one snapshot."""
        with self.connect() as db:
            rows = db.execute(
                """WITH page AS (
                       SELECT p.seq, c.id AS case_id, c.batch_id
                       FROM case_projection p JOIN review_cases c ON c.id = p.case_id
                       WHERE p.seq > ? AND p.seq <= ?
                       ORDER BY p.seq LIMIT ?
                   )
                   SELECT p.seq, p.case_id, p.batch_id, e.event_id, g.kind, g.entity_id
                   FROM page p
                   LEFT JOIN case_evidence e ON e.case_id = p.case_id AND e.batch_id = p.batch_id
                   LEFT JOIN graph_edges g ON g.batch_id = e.batch_id AND g.event_id = e.event_id
                   ORDER BY p.seq, e.event_id, g.kind, g.entity_id""",
                (after, through, limit),
            )
            snapshots = []
            for seq, case_rows in groupby(rows, key=lambda row: row["seq"]):
                events = []
                for event_id, event_rows in groupby(case_rows, key=lambda row: row["event_id"]):
                    entities = []
                    for row in event_rows:
                        case_id = row["case_id"]
                        batch_id = row["batch_id"]
                        if row["kind"] is not None:
                            entities.append({"type": row["kind"], "id": row["entity_id"]})
                    if event_id is not None:
                        events.append({"event_id": event_id, "entities": entities})
                snapshots.append({
                    "seq": seq, "case_id": case_id, "batch_id": batch_id, "events": events,
                })
            return snapshots

    def list_cases(self, state, limit, after):
        with self.connect() as db:
            if state is None:
                rows = db.execute(
                    "SELECT * FROM review_cases WHERE id > ? ORDER BY id LIMIT ?", (after, limit)
                )
            else:
                rows = db.execute(
                    "SELECT * FROM review_cases WHERE state = ? AND id > ? ORDER BY id LIMIT ?",
                    (state, after, limit),
                )
            return [dict(row) for row in rows]

    def get_case(self, case_id):
        with self.connect() as db:
            db.execute("BEGIN")
            case = self._lookup(db, case_id)
            graph = self._graph_edges(db, case)
            evidence = db.execute(
                """SELECT p.event_id, p.amount, p.event_time FROM payment_events p
                   JOIN case_evidence e ON p.batch_id = e.batch_id AND p.event_id = e.event_id
                   WHERE e.case_id = ? AND e.batch_id = ? ORDER BY p.event_time, p.event_id""",
                (case_id, case["batch_id"]),
            )
            history = db.execute(
                "SELECT * FROM review_history WHERE case_id = ? ORDER BY version", (case_id,)
            )
            return {
                **dict(case),
                "transactions": [dict(row) for row in evidence],
                "graph_edges": [dict(row) for row in graph],
                "history": [dict(row) for row in history],
            }

    @staticmethod
    def _graph_edges(db, case):
        return db.execute(
            """SELECT g.event_id, g.kind, g.entity_id FROM graph_edges g
               JOIN case_evidence e ON g.batch_id = e.batch_id AND g.event_id = e.event_id
               WHERE e.case_id = ? AND e.batch_id = ?
               ORDER BY g.event_id, g.kind, g.entity_id""",
            (case["id"], case["batch_id"]),
        )

    def get_case_graph(self, case_id, anchor_type, anchor_id, hops, limit):
        # A case snapshot contains at most 500 events with seven entity fields
        # each. Load only that snapshot before traversing its typed links.
        with self.connect() as db:
            db.execute("BEGIN")
            case = self._lookup(db, case_id)
            neighbors = defaultdict(set)
            for edge in self._graph_edges(db, case):
                event = ("event", edge["event_id"])
                entity = (edge["kind"], edge["entity_id"])
                neighbors[event].add(entity)
                neighbors[entity].add(event)

        anchor = (anchor_type, anchor_id) if anchor_type is not None else min(
            (node for node in neighbors if node[0] == "event"), default=None,
        )
        if anchor is None or anchor not in neighbors:
            raise KeyError(anchor)

        # Insertion order preserves breadth-first order in the response.
        selected = {anchor: 0}
        queue = deque([anchor])
        while queue and len(selected) < limit:
            node = queue.popleft()
            depth = selected[node]
            if depth >= hops:
                continue
            for neighbor in sorted(neighbors[node]):
                if neighbor in selected:
                    continue
                selected[neighbor] = depth + 1
                queue.append(neighbor)
                if len(selected) == limit:
                    break

        def node_id(node):
            return f"{node[0]}:{node[1]}"

        visible_nodes = selected.keys()
        return {
            "case_id": case_id,
            "anchor": {"type": anchor[0], "id": anchor[1]},
            "hops": hops,
            "truncated": any(neighbors[node] - visible_nodes for node in visible_nodes),
            "nodes": [
                {"id": node_id(node), "type": node[0], "label": node[1]}
                for node in selected
            ],
            "edges": [
                {"source": node_id(node), "target": node_id(neighbor), "type": neighbor[0]}
                for node in selected
                if node[0] == "event"
                for neighbor in sorted(neighbors[node] & visible_nodes)
            ],
        }

    def transition(self, case_id, version, state, analyst_id, reason):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._lookup(db, case_id)
            if case["version"] != version:
                raise Conflict("The case changed. Reload it before submitting a decision.")
            if state not in self.TRANSITIONS[case["state"]]:
                raise Conflict(f"Cannot transition from {case['state']} to {state}.")
            db.execute(
                "UPDATE review_cases SET state = ?, version = ? WHERE id = ?",
                (state, version + 1, case_id),
            )
            db.execute(
                "INSERT INTO review_history VALUES (?, ?, ?, ?, ?, ?, ?)",
                (case_id, version + 1, analyst_id, case["state"], state, reason,
                 datetime.now(timezone.utc).isoformat(timespec="milliseconds")),
            )
            return dict(self._lookup(db, case_id))

    @staticmethod
    def _lookup(db, case_id):
        case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            raise KeyError(case_id)
        return case
