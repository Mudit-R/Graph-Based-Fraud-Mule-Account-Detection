import assert from "node:assert/strict";
import { test } from "node:test";
import { linkedCasePaths } from "../src/api/analyst/graph-paths.mjs";

const graph = {
  related_cases: [{ id: "west" }, { id: "south" }, { id: "unconnected" }],
  nodes: [
    { id: "device:shared", type: "device_id" },
    { id: "bank:beneficiary", type: "bank_account_id" },
    { id: "event:east:1", type: "event", case_id: "east" },
    { id: "event:west:1", type: "event", case_id: "west" },
    { id: "event:west:2", type: "event", case_id: "west" },
    { id: "event:south:1", type: "event", case_id: "south" },
    { id: "event:else:1", type: "event", case_id: "unconnected" },
  ],
  edges: [
    { source: "event:east:1", target: "device:shared" },
    { source: "event:west:1", target: "device:shared" },
    { source: "event:west:1", target: "bank:beneficiary" },
    { source: "event:west:2", target: "device:shared" },
    { source: "event:west:2", target: "bank:beneficiary" },
    { source: "event:south:1", target: "bank:beneficiary" },
  ],
};

test("finds a shortest visible path into every reachable linked case", () => {
  const paths = linkedCasePaths(graph, "device:shared");
  assert.deepEqual(paths.get("west").map(({ id }) => id), ["device:shared", "event:west:1"]);
  assert.deepEqual(paths.get("south").map(({ id }) => id), [
    "device:shared", "event:west:1", "bank:beneficiary", "event:south:1",
  ]);
  assert.equal(paths.has("unconnected"), false);
});

test("an absent anchor never yields a path", () => {
  assert.equal(linkedCasePaths(graph, "ip:shared").size, 0);
});
