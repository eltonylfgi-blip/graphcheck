"""Human-readable and Mermaid renderers."""

from __future__ import annotations

from typing import Any

from .core import AuditResult


def render_text(result: AuditResult) -> str:
    lines = [
        f"GraphCheck: {result.status.upper()}",
        (
            f"errors={result.summary['errors']} warnings={result.summary['warnings']} "
            f"declared_edges={result.summary['declared_edges']} "
            f"observed_transition_events={result.summary['observed_transition_events']}"
        ),
        "",
        "Diagnostics:",
    ]
    if result.diagnostics:
        for item in result.diagnostics:
            lines.append(f"- {item.severity.upper()} {item.code}: {item.message}")
    else:
        lines.append("- none")

    lines.extend(["", "Metrics:"])
    timing = result.metrics["timing"]
    if timing["available"]:
        lines.extend(
            [
                f"- duration_total_seconds: {timing['duration_total_seconds']:.6g}",
                f"- sum_node_durations_seconds: {timing['sum_node_durations_seconds']:.6g}",
                (
                    "- approx_parallelism: unavailable (zero wall-clock span)"
                    if timing["approx_parallelism"] is None
                    else f"- approx_parallelism: {timing['approx_parallelism']:.6g}"
                ),
                f"  {timing['approx_parallelism_explanation']}",
            ]
        )
        critical = timing["critical_path_approx"]
        if critical["available"]:
            lines.append(
                f"- critical_path_approx: {' -> '.join(critical['nodes'])} ({critical['seconds']:.6g}s)"
            )
            lines.append(f"  {critical['method']}")
        else:
            lines.append(f"- critical_path_approx: unavailable ({critical['reason']})")
    else:
        lines.append(f"- timing: unavailable ({timing['reason']})")

    cost = result.metrics["cost"]
    if cost["available"]:
        lines.append(f"- cost_total: {cost['total']:.6g} ({cost['unit']})")
    else:
        lines.append(f"- cost: unavailable ({cost['reason']})")
    return "\n".join(lines)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "\\\"").replace("\n", " ")


def render_mermaid(graph: dict[str, Any], result: AuditResult) -> str:
    """Render the declared graph plus trace mismatches as a Mermaid flowchart."""

    declared = set(result.declared_edges)
    observed = {
        (event["from"], event["to"]) for event in result.observed_transitions
    }
    unobserved = declared - observed
    undeclared = observed - declared
    missing_evidence = {
        (item.edge["from"], item.edge["to"])
        for item in result.diagnostics
        if item.code == "MISSING_REQUIRED_EVIDENCE" and item.edge is not None
    }

    node_ids = [node["id"] for node in graph["nodes"]]
    for source, target in sorted(undeclared):
        if source not in node_ids:
            node_ids.append(source)
        if target not in node_ids:
            node_ids.append(target)
    mermaid_ids = {node_id: f"n{index}" for index, node_id in enumerate(node_ids)}

    lines = ["flowchart LR"]
    for node_id in node_ids:
        lines.append(f'  {mermaid_ids[node_id]}["{_escape_label(node_id)}"]')

    link_styles: list[tuple[int, str]] = []
    link_index = 0
    for source, target in result.declared_edges:
        source_id = mermaid_ids[source]
        target_id = mermaid_ids[target]
        edge_key = (source, target)
        if edge_key in missing_evidence:
            lines.append(f"  {source_id} -->|missing evidence| {target_id}")
            link_styles.append((link_index, "stroke:#d33,stroke-width:3px"))
        elif edge_key in unobserved:
            lines.append(f"  {source_id} -.->|not observed| {target_id}")
            link_styles.append((link_index, "stroke:#d98b00,stroke-width:2px"))
        else:
            lines.append(f"  {source_id} --> {target_id}")
        link_index += 1

    for source, target in sorted(undeclared):
        lines.append(
            f"  {mermaid_ids[source]} -.->|undeclared transition| {mermaid_ids[target]}"
        )
        link_styles.append((link_index, "stroke:#d33,stroke-width:3px"))
        link_index += 1

    for index, style in link_styles:
        lines.append(f"  linkStyle {index} {style}")
    lines.append("  %% Red: undeclared transition or required evidence missing")
    lines.append("  %% Orange: declared edge not observed in this trace (not proof of impossibility)")
    return "\n".join(lines)
