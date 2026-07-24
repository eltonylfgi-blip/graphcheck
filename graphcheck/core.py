"""Core validation and audit logic for GraphCheck.

GraphCheck is intentionally conservative: an edge is observed only when the
trace contains an explicit ``transition`` event. Event ordering is never used
to invent a transition.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable


class InputError(ValueError):
    """Raised when a graph or trace does not match the documented schema."""


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    node: str | None = None
    edge: dict[str, str] | None = None
    event_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditResult:
    diagnostics: list[Diagnostic]
    summary: dict[str, int]
    metrics: dict[str, Any]
    declared_edges: list[tuple[str, str]]
    observed_transitions: list[dict[str, Any]]

    @property
    def has_errors(self) -> bool:
        return self.summary["errors"] > 0

    @property
    def status(self) -> str:
        return "fail" if self.has_errors else "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "summary": self.summary,
            "diagnostics": [asdict(item) for item in self.diagnostics],
            "metrics": self.metrics,
            "declared_edges": [
                {"from": source, "to": target}
                for source, target in self.declared_edges
            ],
            "observed_transitions": self.observed_transitions,
        }


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise InputError(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_timestamp(value: Any, location: str) -> datetime:
    _expect(isinstance(value, str) and bool(value.strip()), f"{location} must be a timestamp string")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InputError(f"{location} is not a valid ISO 8601 timestamp") from exc
    _expect(parsed.tzinfo is not None, f"{location} must include a UTC offset or Z")
    return parsed


def _validate_evidence(value: Any, location: str) -> list[str]:
    if value is None:
        return []
    _expect(isinstance(value, list), f"{location} must be a list of evidence IDs")
    _expect(
        all(isinstance(item, str) and bool(item.strip()) for item in value),
        f"{location} must contain only non-empty strings",
    )
    _expect(len(set(value)) == len(value), f"{location} must not contain duplicate IDs")
    return list(value)


def _valid_budget(value: Any) -> bool:
    if _is_number(value):
        return value > 0
    if isinstance(value, dict) and value:
        numeric_values = [item for item in value.values() if _is_number(item)]
        return bool(numeric_values) and all(item > 0 for item in numeric_values)
    return False


def validate_graph(graph: Any) -> None:
    _expect(isinstance(graph, dict), "graph must be a JSON object")
    _expect(graph.get("version") == 1, "graph.version must be 1")

    nodes = graph.get("nodes")
    _expect(isinstance(nodes, list) and bool(nodes), "graph.nodes must be a non-empty list")
    node_ids: list[str] = []
    for index, node in enumerate(nodes):
        location = f"graph.nodes[{index}]"
        _expect(isinstance(node, dict), f"{location} must be an object")
        node_id = node.get("id")
        _expect(isinstance(node_id, str) and bool(node_id.strip()), f"{location}.id must be a non-empty string")
        node_ids.append(node_id)
        if "side_effect" in node:
            _expect(isinstance(node["side_effect"], bool), f"{location}.side_effect must be a boolean")
        for key in ("reducer", "approval", "idempotency_key"):
            if key in node:
                _expect(
                    isinstance(node[key], str) and bool(node[key].strip()),
                    f"{location}.{key} must be a non-empty string",
                )

    _expect(len(set(node_ids)) == len(node_ids), "graph node IDs must be unique")
    node_id_set = set(node_ids)
    for index, node in enumerate(nodes):
        if "reducer" in node:
            _expect(
                node["reducer"] in node_id_set,
                f"graph.nodes[{index}].reducer must reference a declared node",
            )

    edges = graph.get("edges")
    _expect(isinstance(edges, list), "graph.edges must be a list")
    edge_keys: list[tuple[str, str]] = []
    for index, edge in enumerate(edges):
        location = f"graph.edges[{index}]"
        _expect(isinstance(edge, dict), f"{location} must be an object")
        source = edge.get("from")
        target = edge.get("to")
        _expect(isinstance(source, str) and bool(source.strip()), f"{location}.from must be a non-empty string")
        _expect(isinstance(target, str) and bool(target.strip()), f"{location}.to must be a non-empty string")
        _expect(source in node_id_set, f"{location}.from must reference a declared node")
        _expect(target in node_id_set, f"{location}.to must reference a declared node")
        _validate_evidence(edge.get("required_evidence"), f"{location}.required_evidence")
        edge_keys.append((source, target))
    _expect(len(set(edge_keys)) == len(edge_keys), "graph edges must be unique by from/to")

    if "max_iterations" in graph:
        value = graph["max_iterations"]
        _expect(
            isinstance(value, int) and not isinstance(value, bool) and value > 0,
            "graph.max_iterations must be a positive integer",
        )
    if "budget" in graph:
        _expect(_valid_budget(graph["budget"]), "graph.budget must be positive or contain positive numeric limits")


def validate_trace(trace: Any) -> None:
    _expect(isinstance(trace, dict), "trace must be a JSON object")
    _expect(trace.get("version") == 1, "trace.version must be 1")
    events = trace.get("events")
    _expect(isinstance(events, list), "trace.events must be a list")

    event_ids: list[str] = []
    for index, event in enumerate(events):
        location = f"trace.events[{index}]"
        _expect(isinstance(event, dict), f"{location} must be an object")
        event_id = event.get("id")
        _expect(isinstance(event_id, str) and bool(event_id.strip()), f"{location}.id must be a non-empty string")
        event_ids.append(event_id)
        event_type = event.get("type")
        _expect(event_type in {"node", "transition"}, f"{location}.type must be 'node' or 'transition'")

        if event_type == "node":
            node_id = event.get("node")
            _expect(isinstance(node_id, str) and bool(node_id.strip()), f"{location}.node must be a non-empty string")
            has_start = "started_at" in event
            has_end = "ended_at" in event
            if has_start:
                start = _parse_timestamp(event["started_at"], f"{location}.started_at")
            if has_end:
                end = _parse_timestamp(event["ended_at"], f"{location}.ended_at")
            if has_start and has_end:
                _expect(end >= start, f"{location}.ended_at must not precede started_at")
            if "cost" in event:
                _expect(_is_number(event["cost"]) and event["cost"] >= 0, f"{location}.cost must be a non-negative number")
        else:
            for key in ("from", "to"):
                _expect(
                    isinstance(event.get(key), str) and bool(event[key].strip()),
                    f"{location}.{key} must be a non-empty string",
                )
            _validate_evidence(event.get("evidence"), f"{location}.evidence")
            if "timestamp" in event:
                _parse_timestamp(event["timestamp"], f"{location}.timestamp")

    _expect(len(set(event_ids)) == len(event_ids), "trace event IDs must be unique")


def _cyclic_components(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[list[str]]:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)
        adjacency.setdefault(target, [])

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in adjacency[node]:
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])

        if lowlinks[node] == indexes[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            has_self_loop = len(component) == 1 and component[0] in adjacency[component[0]]
            if len(component) > 1 or has_self_loop:
                components.append(sorted(component))

    for node in adjacency:
        if node not in indexes:
            visit(node)
    return sorted(components)


def _timing_metrics(node_events: list[dict[str, Any]], transitions: list[dict[str, Any]]) -> dict[str, Any]:
    if not node_events:
        return {"available": False, "reason": "trace has no node events"}
    if not all("started_at" in event and "ended_at" in event for event in node_events):
        return {
            "available": False,
            "reason": "every node event needs started_at and ended_at; partial traces are not extrapolated",
            "timed_events": sum(
                1 for event in node_events if "started_at" in event and "ended_at" in event
            ),
            "node_events": len(node_events),
        }

    intervals: list[tuple[datetime, datetime, str]] = []
    duration_by_node: dict[str, float] = {}
    for event in node_events:
        start = _parse_timestamp(event["started_at"], "node event started_at")
        end = _parse_timestamp(event["ended_at"], "node event ended_at")
        seconds = (end - start).total_seconds()
        intervals.append((start, end, event["node"]))
        duration_by_node[event["node"]] = duration_by_node.get(event["node"], 0.0) + seconds

    earliest = min(item[0] for item in intervals)
    latest = max(item[1] for item in intervals)
    wall_seconds = (latest - earliest).total_seconds()
    work_seconds = sum((end - start).total_seconds() for start, end, _ in intervals)
    parallelism = work_seconds / wall_seconds if wall_seconds > 0 else None

    observed_edges = {(event["from"], event["to"]) for event in transitions}
    executed_nodes = set(duration_by_node)
    path_edges = {
        edge for edge in observed_edges if edge[0] in executed_nodes and edge[1] in executed_nodes
    }
    cycles = _cyclic_components(executed_nodes, path_edges)
    if cycles:
        critical_path: dict[str, Any] = {
            "available": False,
            "reason": "the observed node-level transition graph is cyclic; event instance links are required for an exact path",
            "cycles": cycles,
        }
    else:
        adjacency: dict[str, list[str]] = {node: [] for node in executed_nodes}
        indegree: dict[str, int] = {node: 0 for node in executed_nodes}
        for source, target in path_edges:
            adjacency[source].append(target)
            indegree[target] += 1
        queue = sorted(node for node, degree in indegree.items() if degree == 0)
        best: dict[str, tuple[float, list[str]]] = {
            node: (duration_by_node[node], [node]) for node in executed_nodes
        }
        while queue:
            node = queue.pop(0)
            for target in adjacency[node]:
                candidate = (
                    best[node][0] + duration_by_node[target],
                    best[node][1] + [target],
                )
                if candidate[0] > best[target][0]:
                    best[target] = candidate
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
                    queue.sort()
        seconds, path = max(best.values(), key=lambda item: (item[0], item[1]))
        critical_path = {
            "available": True,
            "seconds": seconds,
            "nodes": path,
            "method": "longest explicit observed transition chain, weighted by summed run duration per node; wait/queue time is excluded",
        }

    return {
        "available": True,
        "duration_total_seconds": wall_seconds,
        "sum_node_durations_seconds": work_seconds,
        "approx_parallelism": parallelism,
        "approx_parallelism_explanation": "sum of node durations divided by wall-clock span; it is work overlap, not worker utilization",
        "critical_path_approx": critical_path,
    }


def _cost_metrics(node_events: list[dict[str, Any]]) -> dict[str, Any]:
    if not node_events:
        return {"available": False, "reason": "trace has no node events"}
    if not all("cost" in event for event in node_events):
        return {
            "available": False,
            "reason": "every node event needs cost; partial costs are not extrapolated",
            "costed_events": sum(1 for event in node_events if "cost" in event),
            "node_events": len(node_events),
        }
    return {
        "available": True,
        "total": sum(float(event["cost"]) for event in node_events),
        "unit": "trace-defined; GraphCheck does not assume a currency",
    }


def audit(graph: dict[str, Any], trace: dict[str, Any]) -> AuditResult:
    """Validate inputs and compare declared edges with explicit trace events."""

    validate_graph(graph)
    validate_trace(trace)

    diagnostics: list[Diagnostic] = []
    node_by_id = {node["id"]: node for node in graph["nodes"]}
    declared_edge_objects = {
        (edge["from"], edge["to"]): edge for edge in graph["edges"]
    }
    declared_edges = list(declared_edge_objects)
    node_events = [event for event in trace["events"] if event["type"] == "node"]
    transitions = [event for event in trace["events"] if event["type"] == "transition"]
    observed_edges = {(event["from"], event["to"]) for event in transitions}

    for source, target in declared_edges:
        if (source, target) not in observed_edges:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="EDGE_NOT_OBSERVED",
                    message=(
                        f"Declared edge {source!r} -> {target!r} was not observed in this trace; "
                        "this does not prove the edge can never occur."
                    ),
                    edge={"from": source, "to": target},
                )
            )

    for source, target in sorted(observed_edges - set(declared_edges)):
        matching_ids = [
            event["id"]
            for event in transitions
            if event["from"] == source and event["to"] == target
        ]
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="UNDECLARED_TRANSITION",
                message=f"Observed transition {source!r} -> {target!r} is not declared in the graph.",
                edge={"from": source, "to": target},
                details={"event_ids": matching_ids},
            )
        )

    for event in transitions:
        edge_key = (event["from"], event["to"])
        edge = declared_edge_objects.get(edge_key)
        if edge is None:
            continue
        required = set(edge.get("required_evidence", []))
        present = set(event.get("evidence", []))
        missing = sorted(required - present)
        if missing:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="MISSING_REQUIRED_EVIDENCE",
                    message=(
                        f"Transition event {event['id']!r} lacks required evidence: "
                        f"{', '.join(missing)}."
                    ),
                    edge={"from": event["from"], "to": event["to"]},
                    event_id=event["id"],
                    details={"missing_evidence": missing},
                )
            )

    for event in node_events:
        if event["node"] not in node_by_id:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="UNDECLARED_NODE_EVENT",
                    message=f"Node event {event['id']!r} references undeclared node {event['node']!r}.",
                    node=event["node"],
                    event_id=event["id"],
                )
            )

    cycles = _cyclic_components(node_by_id, declared_edges)
    has_global_limit = "max_iterations" in graph or "budget" in graph
    if cycles and not has_global_limit:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="CYCLE_WITHOUT_LIMIT",
                message="Declared graph contains a cycle but has neither max_iterations nor budget.",
                details={"cyclic_components": cycles},
            )
        )

    outgoing: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for source, target in declared_edges:
        outgoing[source].add(target)
    for node_id, targets in sorted(outgoing.items()):
        if len(targets) > 1 and "reducer" not in node_by_id[node_id]:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="FANOUT_WITHOUT_REDUCER",
                    message=(
                        f"Node {node_id!r} fans out to {len(targets)} nodes but does not name an explicit reducer."
                    ),
                    node=node_id,
                    details={"targets": sorted(targets)},
                )
            )

    for node_id, node in sorted(node_by_id.items()):
        if not node.get("side_effect", False):
            continue
        missing_guards = [
            key for key in ("approval", "idempotency_key") if not node.get(key)
        ]
        if missing_guards:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="UNGUARDED_SIDE_EFFECT",
                    message=(
                        f"Side-effect node {node_id!r} is missing: {', '.join(missing_guards)}."
                    ),
                    node=node_id,
                    details={"missing_guards": missing_guards},
                )
            )

    diagnostics.sort(
        key=lambda item: (
            0 if item.severity == "error" else 1,
            item.code,
            item.node or "",
            (item.edge or {}).get("from", ""),
            (item.edge or {}).get("to", ""),
            item.event_id or "",
        )
    )
    errors = sum(item.severity == "error" for item in diagnostics)
    warnings = sum(item.severity == "warning" for item in diagnostics)
    metrics = {
        "timing": _timing_metrics(node_events, transitions),
        "cost": _cost_metrics(node_events),
    }
    observed_transition_view = [
        {
            "id": event["id"],
            "from": event["from"],
            "to": event["to"],
            "evidence": list(event.get("evidence", [])),
        }
        for event in transitions
    ]
    return AuditResult(
        diagnostics=diagnostics,
        summary={
            "errors": errors,
            "warnings": warnings,
            "declared_edges": len(declared_edges),
            "observed_transition_events": len(transitions),
        },
        metrics=metrics,
        declared_edges=declared_edges,
        observed_transitions=observed_transition_view,
    )
