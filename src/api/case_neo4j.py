"""Replay case snapshots to Neo4j and browse typed cross-case evidence."""

from threading import Lock
from urllib.parse import quote


SCHEMA = (
    """CREATE CONSTRAINT fraud_workspace_id IF NOT EXISTS
       FOR (w:FraudGraphWorkspace) REQUIRE w.id IS UNIQUE""",
    """CREATE CONSTRAINT fraud_case_id IF NOT EXISTS
       FOR (c:FraudGraphCase) REQUIRE (c.workspace_id, c.id) IS UNIQUE""",
    """CREATE CONSTRAINT fraud_evidence_node_id IF NOT EXISTS
       FOR (n:FraudGraphNode) REQUIRE (n.workspace_id, n.id) IS UNIQUE""",
)

_READ_CURSOR = """
MATCH (w:FraudGraphWorkspace {id: $workspace_id})
RETURN w.sequence AS sequence
"""
_LOCK_CURSOR = """
MERGE (w:FraudGraphWorkspace {id: $workspace_id})
ON CREATE SET w.sequence = 0
SET w.sequence = w.sequence + 0
RETURN w.sequence AS sequence
"""
_ADVANCE_CURSOR = """
MATCH (w:FraudGraphWorkspace {id: $workspace_id})
SET w.sequence = $sequence
"""
_PROJECT = """
UNWIND $snapshots AS snapshot
WITH snapshot ORDER BY snapshot.seq
MERGE (c:FraudGraphCase {
    workspace_id: $workspace_id, id: snapshot.case_id,
    batch_id: snapshot.batch_id, sequence: snapshot.seq
})
WITH c, snapshot
UNWIND snapshot.events AS event
MERGE (p:FraudGraphNode {
    workspace_id: $workspace_id, id: event.node_id, type: 'event',
    label: event.event_id, case_id: c.id, batch_id: c.batch_id, sequence: snapshot.seq
})
MERGE (c)-[:FRAUD_CASE_PAYMENT]->(p)
FOREACH (entity IN event.entities |
    MERGE (x:FraudGraphNode {workspace_id: $workspace_id, id: entity.node_id})
    ON CREATE SET x.type = entity.type, x.label = entity.id, x.sequence = snapshot.seq
    MERGE (p)-[:FRAUD_EVIDENCE]->(x)
)
"""
_EVENT_ANCHOR = """
MATCH (c:FraudGraphCase {workspace_id: $workspace_id, id: $case_id})
      -[:FRAUD_CASE_PAYMENT]->(p:FraudGraphNode {workspace_id: $workspace_id})
WHERE c.sequence <= $sequence AND ($anchor_id IS NULL OR p.label = $anchor_id)
RETURN p{.id, .type, .label, .case_id, .batch_id} AS node
ORDER BY p.label
LIMIT 1
"""
_ENTITY_ANCHOR = """
MATCH (c:FraudGraphCase {workspace_id: $workspace_id, id: $case_id})
      -[:FRAUD_CASE_PAYMENT]->(:FraudGraphNode {workspace_id: $workspace_id})
      -[:FRAUD_EVIDENCE]->(x:FraudGraphNode {workspace_id: $workspace_id, id: $anchor_id})
WHERE c.sequence <= $sequence
RETURN x{.id, .type, .label} AS node
LIMIT 1
"""
_EXPAND_FRONTIER = """
UNWIND $frontier AS id
MATCH (start:FraudGraphNode {workspace_id: $workspace_id, id: id})
      -[:FRAUD_EVIDENCE]-(neighbor:FraudGraphNode {workspace_id: $workspace_id})
WHERE neighbor.sequence <= $sequence AND NOT neighbor.id IN $seen
WITH DISTINCT neighbor ORDER BY neighbor.id LIMIT $limit
RETURN neighbor{.id, .type, .label, .case_id, .batch_id} AS node
"""
_VISIBLE_EDGES = """
UNWIND $payments AS id
MATCH (payment:FraudGraphNode {workspace_id: $workspace_id, id: id})
      -[:FRAUD_EVIDENCE]->(entity:FraudGraphNode {workspace_id: $workspace_id})
WHERE entity.id IN $entities
RETURN payment.id AS source, entity.id AS target, entity.type AS type
ORDER BY source, target
"""


def _event_node_id(batch_id: str, event_id: str) -> str:
    return f"event:{quote(batch_id, safe='')}:{quote(event_id, safe='')}"


def _prepare_snapshots(snapshots: list[dict]) -> list[dict]:
    prepared = []
    previous = 0
    for snapshot in snapshots:
        sequence = snapshot["seq"]
        if type(sequence) is not int or sequence <= previous:
            raise ValueError("Projection snapshots must have strictly increasing positive sequences.")
        previous = sequence
        events = [
            {
                **event,
                "node_id": _event_node_id(snapshot["batch_id"], event["event_id"]),
                "entities": [
                    {**entity, "node_id": f"{entity['type']}:{entity['id']}"}
                    for entity in event["entities"]
                ],
            }
            for event in snapshot["events"]
        ]
        prepared.append({**snapshot, "events": events})
    return prepared


class Neo4jCaseGraph:
    """A workspace-isolated materialized graph of immutable SQLite snapshots."""

    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(
            uri, auth=(username, password),
            connection_timeout=3.0, connection_acquisition_timeout=5.0, max_transaction_retry_time=3.0,
        )
        self._database = database
        self._schema_ready = False
        self._schema_lock = Lock()

    def close(self) -> None:
        self._driver.close()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                with self._driver.session(database=self._database) as session:
                    for statement in SCHEMA:
                        session.run(statement).consume()
                self._schema_ready = True

    @staticmethod
    def _cursor(tx, workspace_id: str) -> int:
        record = tx.run(_READ_CURSOR, workspace_id=workspace_id).single()
        return record["sequence"] if record is not None else 0

    def get_cursor(self, workspace_id: str) -> int:
        self._ensure_schema()
        with self._driver.session(database=self._database) as session:
            return session.execute_read(self._cursor, workspace_id)

    def project(self, workspace_id: str, snapshots: list[dict]) -> None:
        prepared = _prepare_snapshots(snapshots)
        if not prepared:
            return
        self._ensure_schema()
        with self._driver.session(database=self._database) as session:
            session.execute_write(self._project, workspace_id, prepared)

    @staticmethod
    def _project(tx, workspace_id: str, snapshots: list[dict]) -> None:
        # A dependent property update obtains the workspace write lock before
        # reading its cursor. Projected nodes, relationships and sequence then
        # commit together even if competing writers overlap their input.
        cursor = tx.run(_LOCK_CURSOR, workspace_id=workspace_id).single(strict=True)["sequence"]
        pending = [snapshot for snapshot in snapshots if snapshot["seq"] > cursor]
        if pending:
            tx.run(_PROJECT, workspace_id=workspace_id, snapshots=pending).consume()
            tx.run(_ADVANCE_CURSOR, workspace_id=workspace_id, sequence=pending[-1]["seq"]).consume()

    def related_graph(
        self,
        workspace_id: str,
        case_id: str,
        anchor_type: str | None,
        anchor_id: str | None,
        hops: int,
        limit: int,
    ) -> dict:
        if not 0 <= hops <= 4 or not 1 <= limit <= 200:
            raise ValueError("Use 0–4 hops and a graph limit of 1–200 nodes.")
        if (anchor_type is None) != (anchor_id is None):
            raise ValueError("Supply both anchor type and anchor ID.")
        self._ensure_schema()
        with self._driver.session(database=self._database) as session:
            return session.execute_read(
                self._related_graph, workspace_id, case_id, anchor_type, anchor_id, hops, limit,
            )

    @classmethod
    def _related_graph(cls, tx, workspace_id, case_id, anchor_type, anchor_id, hops, limit):
        # Neo4j reads are read-committed. Restrict all expansions to the
        # captured cursor so new projections cannot change a graph mid-read.
        sequence = cls._cursor(tx, workspace_id)
        anchor_query = _EVENT_ANCHOR
        anchor_key = anchor_id
        if anchor_type not in (None, "event"):
            anchor_query = _ENTITY_ANCHOR
            anchor_key = f"{anchor_type}:{anchor_id}"
        record = tx.run(
            anchor_query, workspace_id=workspace_id, case_id=case_id,
            anchor_id=anchor_key, sequence=sequence,
        ).single()
        if record is None:
            raise KeyError((case_id, anchor_type, anchor_id))
        anchor = record["node"]
        selected = {anchor["id"]: anchor}
        frontier = [anchor["id"]]
        truncated = False
        for _ in range(hops):
            remaining = limit - len(selected)
            if not frontier or remaining == 0:
                break
            additions = cls._neighbors(tx, workspace_id, sequence, frontier, selected, remaining + 1)
            frontier = [node["id"] for node in additions[:remaining]]
            selected.update((node["id"], node) for node in additions[:remaining])
            if len(additions) > remaining:
                truncated = True
                break
        if frontier and not truncated:
            truncated = bool(cls._neighbors(tx, workspace_id, sequence, frontier, selected, 1))

        nodes = [
            {key: value for key, value in node.items() if value is not None}
            for node in selected.values()
        ]
        payments = [node for node in nodes if node["type"] == "event"]
        related_cases = {node["case_id"]: node["batch_id"] for node in payments
                         if node["case_id"] != case_id}
        edges = tx.run(
            _VISIBLE_EDGES, workspace_id=workspace_id,
            payments=[node["id"] for node in payments],
            entities=[node["id"] for node in nodes if node["type"] != "event"],
        )
        return {
            "case_id": case_id,
            "anchor": {"type": anchor["type"], "id": anchor["label"]},
            "hops": hops,
            "truncated": truncated,
            "nodes": nodes,
            "edges": [dict(row) for row in edges],
            "related_cases": [
                {"id": related_id, "batch_id": related_cases[related_id]}
                for related_id in sorted(related_cases)
            ],
        }

    @staticmethod
    def _neighbors(tx, workspace_id, sequence, frontier, selected, limit):
        return [row["node"] for row in tx.run(
            _EXPAND_FRONTIER, workspace_id=workspace_id, sequence=sequence,
            frontier=frontier, seen=list(selected), limit=limit,
        )]
