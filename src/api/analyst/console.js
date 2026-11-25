import { linkedCasePaths } from "./graph-paths.mjs";

const byId = (id) => document.getElementById(id);
const formatMoney = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const PAGE_SIZE = 25;
const transitions = { open: ["in_review"], in_review: ["escalated", "dismissed"] };

let token = "";
let capabilities = new Set();
let crossCaseAvailable = false;
let activeCase = null;
let selectedAnchor = null;
let graph = null;
let rows = [];
let queueRun = 0;
let caseRun = 0;
let graphRun = 0;
let sessionRun = 0;

function pretty(value) {
  if (value === "event") return "Payment";
  if (value === "ATO_VELOCITY_SURGE") return "ATO velocity surge";
  const text = String(value).replace(/_id$/i, "").replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function utc(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return date.toISOString().slice(0, 19).replace("T", " ");
}

function showNotice(message = "", kind = "info") {
  const notice = byId("notification");
  notice.textContent = message;
  notice.dataset.kind = kind;
  notice.hidden = !message;
}

async function api(path, options = {}) {
  const session = sessionRun;
  const response = await fetch(path, {
    signal: AbortSignal.timeout(10000),
    ...options,
    headers: { Authorization: "Bearer " + token, ...(options.headers ?? {}) },
  });
  if (session !== sessionRun) throw new DOMException("Session ended", "AbortError");
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const firstIssue = Array.isArray(body?.detail) ? body.detail[0] : null;
    const detail = typeof body?.detail === "string" ? body.detail : firstIssue?.msg;
    const error = new Error(detail || "The request could not be completed.");
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function disconnect(message = "") {
  sessionRun++;
  token = "";
  capabilities = new Set();
  crossCaseAvailable = false;
  byId("graph-scope").value = "current";
  clearLinkedCases();
  byId("identity").textContent = "";
  byId("identity").hidden = true;
  byId("batch-file").value = "";
  activeCase = null;
  selectedAnchor = null;
  graph = null;
  rows = [];
  queueRun++;
  caseRun++;
  graphRun++;
  byId("queue-list").replaceChildren();
  byId("graph-nodes").replaceChildren();
  byId("connections").replaceChildren();
  byId("payments").replaceChildren();
  byId("history").replaceChildren();
  byId("workspace").hidden = true;
  byId("case").hidden = true;
  byId("empty").hidden = false;
  byId("reason").value = "";
  byId("token").value = "";
  byId("disconnect").hidden = true;
  byId("connect").hidden = false;
  showNotice(message, "error");
  byId("token").focus();
}

function handleError(error) {
  if (error.name === "AbortError") return;
  if (error.status === 401) {
    disconnect("Enter a valid analyst token to continue.");
    return;
  }
  if (error.name === "TimeoutError") {
    showNotice("The service timed out. Try again.", "error");
    return;
  }
  showNotice(error.message, "error");
}

function button(label, handler) {
  const result = document.createElement("button");
  result.type = "button";
  result.className = "quiet";
  result.textContent = label;
  result.addEventListener("click", handler);
  return result;
}

function highlightQueue() {
  for (const item of byId("queue-list").querySelectorAll("button")) {
    item.setAttribute("aria-pressed", String(item.dataset.case === activeCase?.id));
  }
}

async function loadQueue({ append = false } = {}) {
  if (!capabilities.has("read_cases")) return;
  const run = ++queueRun;
  const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (byId("state-filter").value) query.set("state", byId("state-filter").value);
  if (append && rows.length) query.set("after", rows.at(-1).id);
  const { cases } = await api("/cases?" + query);
  if (run !== queueRun) return;
  rows = append ? [...rows, ...cases] : cases;
  const elements = rows.map((row) => {
    const li = document.createElement("li");
    const control = button("", () => selectCase(row.id));
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    title.textContent = pretty(row.typology);
    meta.textContent = pretty(row.state) + " · " + formatMoney.format(row.exposure);
    control.dataset.case = row.id;
    const batch = document.createElement("span");
    batch.className = "queue-batch";
    batch.textContent = row.batch_id;
    control.append(title, meta, batch);
    li.append(control);
    return li;
  });
  byId("queue-list").replaceChildren(...elements);
  byId("queue-empty").hidden = Boolean(rows.length);
  byId("load-more").hidden = cases.length < PAGE_SIZE;
  highlightQueue();
}

function renderPayments(payments) {
  const rows = payments.map((payment) => {
    const row = document.createElement("tr");
    const id = row.insertCell();
    const select = button(payment.event_id, () => changeAnchor("event", payment.event_id));
    id.append(select);
    row.insertCell().textContent = formatMoney.format(payment.amount);
    row.insertCell().textContent = utc(payment.event_time * 1000);
    return row;
  });
  byId("payments").replaceChildren(...rows);
}

function renderReview(caseData) {
  const options = transitions[caseData.state] ?? [];
  byId("review-form").hidden = !options.length || !capabilities.has("review_cases");
  byId("decision").replaceChildren(...options.map((value) => new Option(pretty(value), value)));
  byId("history-empty").hidden = Boolean(caseData.history.length);
  byId("history").replaceChildren(...caseData.history.map((entry) => {
    const item = document.createElement("li");
    const detail = document.createElement("strong");
    const reason = document.createElement("p");
    detail.textContent = pretty(entry.to_state) + " · " + entry.analyst_id + " · " + utc(entry.recorded_at) + " UTC";
    reason.textContent = entry.reason;
    item.append(detail, reason);
    return item;
  }));
}

function renderCase(data) {
  byId("case-title").textContent = pretty(data.typology);
  byId("case-meta").textContent = "Case " + data.id.slice(0, 10) + " · Batch " + data.batch_id;
  byId("case-state").textContent = pretty(data.state);
  byId("case-risk").textContent = pretty(data.risk);
  byId("case-exposure").textContent = formatMoney.format(data.exposure);
  byId("case-count").textContent = data.transactions.length;
  renderPayments(data.transactions);
  renderReview(data);
  byId("empty").hidden = true;
  byId("case").hidden = false;
  highlightQueue();
}

async function selectCase(id, { scope, anchor } = {}) {
  showNotice();
  const run = ++caseRun;
  graphRun++;
  try {
    const data = await api("/cases/" + encodeURIComponent(id));
    if (run !== caseRun) return;
    if (activeCase?.id !== id) {
      selectedAnchor = null;
      byId("graph-scope").value = "current";
      graph = null;
      clearLinkedCases();
      byId("graph-nodes").replaceChildren();
      byId("connections").replaceChildren();
      byId("reason").value = "";
    }
    if (scope === "connected" && crossCaseAvailable) byId("graph-scope").value = scope;
    if (anchor) selectedAnchor = anchor;
    activeCase = data;
    renderCase(data);
    drawGraph();
    await loadGraph();
  } catch (error) {
    if (run === caseRun) handleError(error);
  }
}

function changeAnchor(type, id) {
  selectedAnchor = { type, id };
  loadGraph();
}

function clearLinkedCases() {
  byId("linked-case-list").replaceChildren();
  byId("linked-cases").hidden = true;
  byId("remote-legend").hidden = true;
}

function nodeLabel(node) {
  const label = pretty(node.type) + ": " + node.label;
  return node.batch_id ? label + " · " + node.batch_id : label;
}

function selectGraphNode(node) {
  if (node.type === "event" && node.case_id && node.case_id !== activeCase.id) {
    return selectCase(node.case_id, { scope: "connected", anchor: { type: "event", id: node.label } });
  }
  if (graph.related_cases && node.type !== "event" &&
      !activeCase.graph_edges.some((edge) => edge.kind === node.type && edge.entity_id === node.label)) {
    const paymentIds = new Set(graph.edges.filter((edge) => edge.target === node.id).map((edge) => edge.source));
    const payment = graph.nodes.find((candidate) => candidate.case_id && paymentIds.has(candidate.id));
    if (payment) {
      return selectCase(payment.case_id, { scope: "connected", anchor: { type: node.type, id: node.label } });
    }
  }
  return changeAnchor(node.type, node.label);
}

async function loadGraph() {
  if (!activeCase) return;
  const run = ++graphRun;
  const id = activeCase.id;
  const connected = crossCaseAvailable && byId("graph-scope").value === "connected";
  const query = new URLSearchParams({ hops: byId("hops").value, limit: "48" });
  if (selectedAnchor) {
    query.set("anchor_type", selectedAnchor.type);
    query.set("anchor_id", selectedAnchor.id);
  }
  byId("retry-graph").hidden = true;
  byId("graph-status").textContent = connected ? "Searching connected cases…" : "Loading linked evidence…";
  clearLinkedCases();
  byId("graph-nodes").replaceChildren();
  byId("connections").replaceChildren();
  graph = null;
  drawGraph();
  try {
    const path = connected ? "/related-graph?" : "/graph?";
    const result = await api("/cases/" + encodeURIComponent(id) + path + query);
    if (run !== graphRun) return;
    graph = result;
    selectedAnchor = result.anchor;
    renderGraph();
  } catch (error) {
    if (run !== graphRun) return;
    graph = null;
    byId("retry-graph").hidden = !connected || error.status !== 503;
    byId("graph-status").textContent = connected
      ? "Cross-case search is unavailable. Switch to Current case to view saved evidence."
      : "Linked evidence is unavailable.";
    byId("graph-nodes").replaceChildren();
    byId("connections").replaceChildren();
    drawGraph();
    handleError(error);
  }
}

function graphAnchor() {
  return graph.nodes.find((node) => node.type === graph.anchor.type && node.label === graph.anchor.id &&
    (!node.case_id || node.case_id === graph.case_id));
}

function renderGraph() {
  const anchor = graphAnchor();
  const labels = new Map(graph.nodes.map((node) => [node.id, nodeLabel(node)]));
  const related = graph.related_cases;
  if (related) {
    const paths = linkedCasePaths(graph, anchor?.id);
    byId("linked-case-list").replaceChildren(...related.map((entry) => {
      const row = document.createElement("li");
      row.append(button("Open batch " + entry.batch_id + " · " + entry.id.slice(0, 8),
        () => selectCase(entry.id, { scope: "connected" })));
      const path = paths.get(entry.id);
      if (path) {
        const evidence = document.createElement("p");
        evidence.className = "case-path";
        evidence.textContent = path.map(nodeLabel).join(" → ");
        row.append(evidence);
      }
      return row;
    }));
    byId("linked-cases").hidden = !related.length;
    byId("remote-legend").hidden = !related.length;
  }
  byId("graph-status").textContent = graph.nodes.length + " nodes · " + graph.edges.length + " links" +
    (related ? " · " + related.length + " linked " + (related.length === 1 ? "case" : "cases") : "") +
    (graph.truncated ? " · More evidence beyond this view" : "");
  byId("graph-nodes").replaceChildren(...graph.nodes.map((node) => {
    const control = button(nodeLabel(node), () => selectGraphNode(node));
    if (node.case_id && node.case_id !== activeCase.id) control.dataset.remote = "true";
    control.setAttribute("aria-pressed", String(node.id === anchor?.id));
    return control;
  }));
  byId("connections").replaceChildren(...graph.edges.map((edge) => {
    const item = document.createElement("li");
    item.textContent = labels.get(edge.source) + " → " + labels.get(edge.target) + " (" + pretty(edge.type) + ")";
    return item;
  }));
  drawGraph();
}

function drawGraph() {
  const canvas = byId("graph");
  const { width, height } = canvas.getBoundingClientRect();
  if (!width || !height) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  if (!graph?.nodes.length) return;

  const anchor = graphAnchor() ?? graph.nodes[0];
  const middle = [width / 2, height / 2];
  const events = graph.nodes.filter((node) => node.type === "event" && node.id !== anchor.id);
  const identities = graph.nodes.filter((node) => node.type !== "event" && node.id !== anchor.id);
  const positions = new Map([[anchor.id, middle]]);
  for (const [group, spread] of [[events, 0.45], [identities, 1]]) {
    group.forEach((node, index) => {
      const angle = 2 * Math.PI * index / group.length - Math.PI / 2;
      positions.set(node.id, [
        middle[0] + Math.cos(angle) * (middle[0] - 21) * spread,
        middle[1] + Math.sin(angle) * (middle[1] - 21) * spread,
      ]);
    });
  }

  context.strokeStyle = "#9db5be";
  for (const edge of graph.edges) {
    const start = positions.get(edge.source);
    const end = positions.get(edge.target);
    if (!start || !end) continue;
    context.beginPath();
    context.moveTo(...start);
    context.lineTo(...end);
    context.stroke();
  }
  for (const node of graph.nodes) {
    const [x, y] = positions.get(node.id);
    const isRemote = node.case_id && node.case_id !== graph.case_id;
    const color = node.type === "event" ? (isRemote ? "#7144a4" : "#179480") : "#6185a7";
    context.fillStyle = node.id === anchor.id ? "#b1650e" : color;
    context.beginPath();
    context.arc(x, y, node.id === anchor.id ? 8 : 5, 0, 2 * Math.PI);
    context.fill();
  }
}

new ResizeObserver(drawGraph).observe(byId("graph"));

byId("connect").addEventListener("submit", async (event) => {
  event.preventDefault();
  sessionRun++;
  token = byId("token").value.trim();
  const submit = event.submitter;
  submit.disabled = true;
  showNotice();
  try {
    const account = await api("/analyst/session");
    capabilities = new Set(account.capabilities);
    crossCaseAvailable = account.cross_case_available === true;
    byId("graph-scope-label").hidden = !crossCaseAvailable;
    byId("load-linked-demo").hidden = !crossCaseAvailable || !capabilities.has("ingest_batches");
    await loadQueue();
    const canRead = capabilities.has("read_cases");
    byId("identity").textContent = account.analyst_id + " · " + pretty(account.role);
    byId("identity").hidden = false;
    byId("queue-title").textContent = canRead ? "Cases" : "Payment intake";
    byId("queue-controls").hidden = !canRead;
    byId("refresh").hidden = !canRead;
    byId("case-shell").hidden = !canRead;
    byId("workspace").classList.toggle("intake-only", !canRead);
    byId("import-form").hidden = !capabilities.has("ingest_batches");
    byId("token").value = "";
    byId("connect").hidden = true;
    byId("workspace").hidden = false;
    byId("disconnect").hidden = false;
    byId(canRead ? "refresh" : "batch-file").focus();
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
});

if (new URLSearchParams(location.search).get("demo") === "1") {
  byId("connect").requestSubmit(byId("connect-button"));
}

byId("disconnect").addEventListener("click", () => disconnect());
byId("refresh").addEventListener("click", () => loadQueue().catch(handleError));
byId("load-more").addEventListener("click", () => loadQueue({ append: true }).catch(handleError));
byId("state-filter").addEventListener("change", () => loadQueue().catch(handleError));
byId("hops").addEventListener("change", loadGraph);
byId("graph-scope").addEventListener("change", loadGraph);
byId("retry-graph").addEventListener("click", loadGraph);
async function importBatch(batch, demo = false) {
  const result = await post("/batches", batch);
  if (capabilities.has("read_cases")) {
    byId("state-filter").value = "";
    await loadQueue();
    if (result.case_ids.length === 1) await selectCase(result.case_ids[0]);
  }
  if (demo) {
    showNotice(result.created ? "Synthetic case added." : "Synthetic batch was already imported.");
    return;
  }
  const count = result.case_ids.length;
  const summary = count + (count === 1 ? " case" : " cases");
  showNotice(result.created ? "Batch imported. " + summary + " created." : "Batch already imported. " + summary + " unchanged.");
}

byId("load-demo").addEventListener("click", async (event) => {
  const control = event.currentTarget;
  control.disabled = true;
  showNotice();
  try {
    await importBatch(await api("/analyst/demo-batch"), true);
  } catch (error) {
    handleError(error);
  } finally {
    control.disabled = false;
  }
});

byId("load-linked-demo").addEventListener("click", async (event) => {
  const control = event.currentTarget;
  control.disabled = true;
  showNotice("Importing linked demo cases…");
  try {
    const { anchor, batches } = await api("/analyst/demo-related-batches");
    let first;
    for (const batch of batches) {
      const result = await post("/batches", batch);
      first ??= result.case_ids[0];
    }
    byId("state-filter").value = "";
    await loadQueue();
    byId("hops").value = "4";
    if (first) await selectCase(first, { scope: "connected", anchor });
    if (graph && activeCase?.id === first) showNotice("Linked demo cases are ready to investigate.");
  } catch (error) {
    handleError(error);
  } finally {
    control.disabled = false;
  }
});

byId("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = byId("batch-file").files[0];
  if (!file || file.size > 1_000_000) {
    showNotice("Choose a JSON file up to 1 MB.", "error");
    return;
  }
  const submit = event.submitter;
  submit.disabled = true;
  const session = sessionRun;
  showNotice();
  try {
    let batch;
    try {
      batch = JSON.parse(await file.text());
    } catch {
      showNotice("The file must contain valid JSON.", "error");
      return;
    }
    if (session !== sessionRun) return;
    await importBatch(batch);
    byId("batch-file").value = "";
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
});

byId("review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeCase) return;
  const id = activeCase.id;
  const viewAtSubmit = caseRun;
  const request = { version: activeCase.version, state: byId("decision").value, reason: byId("reason").value.trim() };
  if (request.reason.length < 3) {
    showNotice("Enter a reason with at least three characters.", "error");
    return;
  }
  const submit = event.submitter;
  submit.disabled = true;
  showNotice();
  try {
    await post("/cases/" + encodeURIComponent(id) + "/reviews", request);
    if (caseRun === viewAtSubmit && activeCase?.id === id) {
      byId("reason").value = "";
      await selectCase(id);
    }
    await loadQueue();
    if (token) showNotice("Review recorded.");
  } catch (error) {
    if (error.status === 409 && token) {
      if (caseRun === viewAtSubmit && activeCase?.id === id) await selectCase(id);
      try {
        await loadQueue();
        showNotice("Another review was recorded. The case and queue are up to date.", "error");
      } catch (refreshError) {
        handleError(refreshError);
      }
    } else {
      handleError(error);
    }
  } finally {
    submit.disabled = false;
  }
});
