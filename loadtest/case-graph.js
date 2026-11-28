import http from "k6/http";
import { check, fail, sleep } from "k6";

const base = (__ENV.BASE_URL || "http://127.0.0.1:8002").replace(/\/$/, "");
const token = __ENV.FRAUD_CASE_TOKEN;
const requestedCaseId = __ENV.CASE_ID;
const hops = __ENV.HOPS || "4";
const limit = __ENV.NODE_LIMIT || "200";

if (!token) {
  throw new Error("Set FRAUD_CASE_TOKEN to a reviewer/admin bearer token before running k6.");
}

export const options = {
  scenarios: {
    repeatable_warm_graph_sample: {
      executor: "shared-iterations",
      vus: 1,
      iterations: 30,
      maxDuration: "3m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
};

const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };

export function setup() {
  let caseId = requestedCaseId;
  if (!caseId) {
    const response = http.get(`${base}/cases?limit=1`, { headers, tags: { phase: "setup" } });
    if (response.status !== 200) {
      fail(`Unable to list cases (HTTP ${response.status}); verify the API URL and reviewer token.`);
    }
    const cases = response.json("cases");
    if (!Array.isArray(cases) || cases.length === 0) {
      fail("No case found. Start the synthetic cross-case fixture or provide CASE_ID.");
    }
    caseId = cases[0].id;
  }
  return { caseId };
}

export default function (data) {
  const response = http.get(
    `${base}/cases/${encodeURIComponent(data.caseId)}/related-graph?hops=${hops}&limit=${limit}`,
    { headers, tags: { endpoint: "related-graph", phase: "warm" } },
  );
  const valid = check(response, {
    "related graph returns 200": (r) => r.status === 200,
    "related graph is valid JSON": (r) => r.headers["Content-Type"]?.includes("application/json"),
    "graph respects configured node limit": (r) => {
      if (r.status !== 200) return false;
      const result = r.json();
      return Array.isArray(result.nodes) && result.nodes.length <= Number(limit)
        && Array.isArray(result.edges);
    },
  });
  if (!valid) {
    fail(`Graph response failed validation (HTTP ${response.status}); check Neo4j projection and case access.`);
  }
  sleep(0.1);
}
