// Find the shortest visible evidence path from the anchor into each linked case.
export function linkedCasePaths(graph, anchorId) {
  const related = new Set(graph.related_cases.map((entry) => entry.id));
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const graphLinks = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const { source, target } of graph.edges) {
    if (!graphLinks.has(source) || !graphLinks.has(target)) continue;
    graphLinks.get(source).push(target);
    graphLinks.get(target).push(source);
  }
  if (!nodes.has(anchorId)) return new Map();

  const parent = new Map([[anchorId, null]]);
  const queue = [anchorId];
  const paths = new Map();
  for (let index = 0; index < queue.length && paths.size < related.size; index++) {
    const id = queue[index];
    const node = nodes.get(id);
    if (node.type === "event" && related.has(node.case_id) && !paths.has(node.case_id)) {
      const path = [];
      for (let cursor = id; cursor !== null; cursor = parent.get(cursor)) {
        path.push(nodes.get(cursor));
      }
      paths.set(node.case_id, path.reverse());
    }
    for (const neighbor of graphLinks.get(id)) {
      if (parent.has(neighbor)) continue;
      parent.set(neighbor, id);
      queue.push(neighbor);
    }
  }
  return paths;
}
