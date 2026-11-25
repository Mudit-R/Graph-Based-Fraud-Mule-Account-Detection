"""Verify the Neo4j projection contract and opt-in real graph behavior."""

from concurrent.futures import ThreadPoolExecutor
import os
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.api import case_neo4j


def snapshot(sequence, case_id, batch_id, *events):
    return {"seq": sequence, "case_id": case_id, "batch_id": batch_id, "events": [
        {"event_id": event_id, "entities": [{"type": kind, "id": entity_id}
                                           for kind, entity_id in entities]}
        for event_id, entities in events
    ]}


def test_snapshot_keys_separate_batches_and_identifier_types():
    original = [
        snapshot(1, "c1", "a:b", ("c", [("device_id", "same"), ("ip_id", "same")])),
        snapshot(2, "c2", "a", ("b:c", [])),
    ]
    prepared = case_neo4j._prepare_snapshots(original)
    first, second = [case["events"][0] for case in prepared]
    assert first["node_id"] == "event:a%3Ab:c"
    assert second["node_id"] == "event:a:b%3Ac"
    assert [entity["node_id"] for entity in first["entities"]] == ["device_id:same", "ip_id:same"]
    assert "node_id" not in original[0]["events"][0]


@pytest.mark.parametrize("sequences", [[0], [1, 1], [2, 1], [True]])
def test_snapshot_cursor_validation(sequences):
    with pytest.raises(ValueError, match="strictly increasing"):
        case_neo4j._prepare_snapshots([snapshot(sequence, "case", "batch") for sequence in sequences])


def test_projection_only_applies_rows_after_the_locked_cursor():
    tx = Mock()
    tx.run.return_value.single.return_value = {"sequence": 7}
    snapshots = [snapshot(7, "c1", "b1"), snapshot(8, "c2", "b2")]
    case_neo4j.Neo4jCaseGraph._project(tx, "workspace", snapshots)
    assert [item.args[0] for item in tx.run.call_args_list] == [
        case_neo4j._LOCK_CURSOR, case_neo4j._PROJECT, case_neo4j._ADVANCE_CURSOR,
    ]
    assert tx.run.call_args_list[1].kwargs == {"workspace_id": "workspace", "snapshots": [snapshots[1]]}
    assert tx.run.call_args_list[2].kwargs == {"workspace_id": "workspace", "sequence": 8}

    tx.reset_mock()
    case_neo4j.Neo4jCaseGraph._project(tx, "workspace", snapshots[:1])
    tx.run.assert_called_once_with(case_neo4j._LOCK_CURSOR, workspace_id="workspace")


def test_bounded_frontier_excludes_discovered_nodes_and_clips_output():
    tx = Mock()
    anchor = {"id": "event:batch:payment", "type": "event", "label": "payment",
              "case_id": "case", "batch_id": "batch"}
    first = {"id": "device_id:device", "type": "device_id", "label": "device"}
    second = {"id": "ip_id:ip", "type": "ip_id", "label": "ip"}
    cursor = Mock()
    cursor.single.return_value = {"sequence": 9}
    anchor_result = Mock()
    anchor_result.single.return_value = {"node": anchor}
    edge = {"source": anchor["id"], "target": first["id"], "type": "device_id"}
    tx.run.side_effect = [cursor, anchor_result, [{"node": first}, {"node": second}], [edge]]

    graph = case_neo4j.Neo4jCaseGraph._related_graph(tx, "workspace", "case", None, None, 4, 2)

    assert graph["truncated"] is True
    assert graph["nodes"] == [anchor, first]
    assert graph["edges"] == [edge]
    expansion = tx.run.call_args_list[2]
    assert expansion.args == (case_neo4j._EXPAND_FRONTIER,)
    assert expansion.kwargs == {
        "workspace_id": "workspace", "sequence": 9, "frontier": [anchor["id"]],
        "seen": [anchor["id"]], "limit": 2,
    }


@pytest.fixture
def real_graph():
    uri = os.getenv("FRAUD_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set FRAUD_TEST_NEO4J_URI to verify against a real Neo4j server.")
    adapter = case_neo4j.Neo4jCaseGraph(
        uri, os.getenv("FRAUD_TEST_NEO4J_USER", "neo4j"),
        os.environ["FRAUD_TEST_NEO4J_PASSWORD"],
        os.getenv("FRAUD_TEST_NEO4J_DATABASE", "neo4j"),
    )
    workspace, other_workspace = (f"test-workspace-{uuid4().hex}" for _ in range(2))
    try:
        assert adapter.get_cursor(workspace) == 0
        try:
            yield adapter, workspace, other_workspace
        finally:
            with adapter._driver.session(database=adapter._database) as session:
                session.run(
                    """MATCH (n)
                       WHERE (n:FraudGraphCase OR n:FraudGraphNode)
                         AND n.workspace_id IN $workspaces
                       DETACH DELETE n""", workspaces=[workspace, other_workspace],
                ).consume()
                session.run(
                    """MATCH (w:FraudGraphWorkspace) WHERE w.id IN $workspaces DETACH DELETE w""",
                    workspaces=[workspace, other_workspace],
                ).consume()
    finally:
        adapter.close()


def test_real_neo4j_projects_and_traverses_with_workspace_isolation(real_graph):
    adapter, workspace, other_workspace = real_graph
    a = snapshot(1, "case-a", "batch:a", ("payment:shared", [("device_id", "shared")]))
    b = snapshot(2, "case-b", "batch:b", (
        "payment:shared", [("device_id", "shared"), ("address_id", "bridge")],
    ))
    c = snapshot(3, "case-c", "batch:c", ("payment:shared", [("ip_id", "shared")]))
    d = snapshot(4, "case-d", "batch:d", ("payment:d", [("address_id", "bridge")]))
    outside = snapshot(1, "outside-case", "batch:a", ("payment:shared", [("device_id", "shared")]))
    assert adapter.get_cursor(workspace) == 0
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda batch: adapter.project(workspace, batch), [[a, b], [a, b, c, d]]))
    adapter.project(workspace, [a, b, c, d])
    adapter.project(other_workspace, [outside])
    assert adapter.get_cursor(workspace) == 4
    assert adapter.get_cursor(other_workspace) == 1

    graph = adapter.related_graph(workspace, "case-a", None, None, 2, 20)
    assert graph["anchor"] == {"type": "event", "id": "payment:shared"}
    assert graph["related_cases"] == [{"id": "case-b", "batch_id": "batch:b"}]
    assert graph["truncated"] is True
    assert {node["id"] for node in graph["nodes"]} == {
        "event:batch%3Aa:payment%3Ashared", "event:batch%3Ab:payment%3Ashared", "device_id:shared",
    }
    assert len(graph["edges"]) == 2

    expanded = adapter.related_graph(workspace, "case-a", None, None, 4, 20)
    assert expanded["related_cases"] == [
        {"id": "case-b", "batch_id": "batch:b"}, {"id": "case-d", "batch_id": "batch:d"},
    ]
    assert len(expanded["nodes"]) == 5
    assert len(expanded["edges"]) == 4
    assert expanded["truncated"] is False

    for case_id, anchor_type, anchor_id in [
        ("case-a", "ip_id", "shared"), ("outside-case", None, None),
    ]:
        with pytest.raises(KeyError):
            adapter.related_graph(workspace, case_id, anchor_type, anchor_id, 2, 20)
    assert adapter.related_graph(other_workspace, "outside-case", None, None, 2, 20)["related_cases"] == []

    clipped = adapter.related_graph(workspace, "case-a", "device_id", "shared", 1, 2)
    assert len(clipped["nodes"]) == 2
    assert len(clipped["edges"]) == 1
    assert clipped["truncated"] is True
    shallow = adapter.related_graph(workspace, "case-a", "device_id", "shared", 0, 20)
    assert shallow["nodes"] == [{"id": "device_id:shared", "type": "device_id", "label": "shared"}]
    assert shallow["related_cases"] == []
    assert shallow["truncated"] is True


def test_real_neo4j_rolls_back_projection_and_cursor_together(real_graph):
    from neo4j.exceptions import Neo4jError

    adapter, workspace, _ = real_graph
    first = snapshot(1, "case-a", "batch", ("payment-1", [("device_id", "device-1")]))
    valid = snapshot(2, "case-b", "batch", ("payment-2", [("device_id", "device-2")]))
    conflicting = snapshot(3, "case-c", "batch", ("payment-1", [("device_id", "device-3")]))
    adapter.project(workspace, [first])
    with pytest.raises(Neo4jError):
        adapter.project(workspace, [valid, conflicting])
    assert adapter.get_cursor(workspace) == 1
    with pytest.raises(KeyError):
        adapter.related_graph(workspace, "case-b", None, None, 1, 20)
    adapter.project(workspace, [valid])
    assert adapter.get_cursor(workspace) == 2
    assert adapter.related_graph(workspace, "case-b", None, None, 1, 20)["anchor"]["id"] == "payment-2"
