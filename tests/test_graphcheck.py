from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from graphcheck import InputError, audit
from graphcheck.render import render_mermaid


ROOT = Path(__file__).resolve().parents[1]


def load_case(name: str) -> tuple[dict, dict]:
    directory = ROOT / "examples" / name
    return (
        json.loads((directory / "graph.json").read_text(encoding="utf-8")),
        json.loads((directory / "trace.json").read_text(encoding="utf-8")),
    )


class AuditTests(unittest.TestCase):
    def test_healthy_case_passes_and_reports_complete_metrics(self) -> None:
        graph, trace = load_case("healthy")

        result = audit(graph, trace)

        self.assertEqual("pass", result.status)
        self.assertEqual([], result.diagnostics)
        timing = result.metrics["timing"]
        self.assertTrue(timing["available"])
        self.assertEqual(9.0, timing["duration_total_seconds"])
        self.assertEqual(12.0, timing["sum_node_durations_seconds"])
        self.assertAlmostEqual(4 / 3, timing["approx_parallelism"])
        self.assertEqual(
            ["scope", "research_a", "reduce", "publish"],
            timing["critical_path_approx"]["nodes"],
        )
        self.assertEqual(9.0, timing["critical_path_approx"]["seconds"])
        self.assertAlmostEqual(0.11, result.metrics["cost"]["total"])

    def test_broken_edges_are_precise_about_observation_and_evidence(self) -> None:
        graph, trace = load_case("broken_edges")

        result = audit(graph, trace)

        codes = [item.code for item in result.diagnostics]
        self.assertIn("UNDECLARED_TRANSITION", codes)
        self.assertIn("MISSING_REQUIRED_EVIDENCE", codes)
        self.assertIn("EDGE_NOT_OBSERVED", codes)
        warning = next(item for item in result.diagnostics if item.code == "EDGE_NOT_OBSERVED")
        self.assertIn("does not prove", warning.message)
        missing = next(
            item for item in result.diagnostics if item.code == "MISSING_REQUIRED_EVIDENCE"
        )
        self.assertEqual(["scope-brief"], missing.details["missing_evidence"])
        self.assertFalse(result.metrics["timing"]["available"])

    def test_missing_limits_case_finds_static_safety_gaps(self) -> None:
        graph, trace = load_case("missing_limits")

        result = audit(graph, trace)

        codes = {item.code for item in result.diagnostics}
        self.assertTrue(
            {
                "CYCLE_WITHOUT_LIMIT",
                "FANOUT_WITHOUT_REDUCER",
                "UNGUARDED_SIDE_EFFECT",
            }.issubset(codes)
        )

    def test_global_iteration_limit_covers_cycle_diagnostic_only(self) -> None:
        graph, trace = load_case("missing_limits")
        graph["max_iterations"] = 3

        result = audit(graph, trace)

        self.assertNotIn("CYCLE_WITHOUT_LIMIT", {item.code for item in result.diagnostics})
        self.assertIn("FANOUT_WITHOUT_REDUCER", {item.code for item in result.diagnostics})

    def test_partial_timing_and_cost_are_not_extrapolated(self) -> None:
        graph = {
            "version": 1,
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"from": "a", "to": "b"}],
        }
        trace = {
            "version": 1,
            "events": [
                {
                    "id": "n1",
                    "type": "node",
                    "node": "a",
                    "started_at": "2026-07-24T08:00:00Z",
                    "ended_at": "2026-07-24T08:00:01Z",
                    "cost": 0.1,
                },
                {"id": "n2", "type": "node", "node": "b"},
                {"id": "t1", "type": "transition", "from": "a", "to": "b"},
            ],
        }

        result = audit(graph, trace)

        self.assertFalse(result.metrics["timing"]["available"])
        self.assertEqual(1, result.metrics["timing"]["timed_events"])
        self.assertFalse(result.metrics["cost"]["available"])
        self.assertEqual(1, result.metrics["cost"]["costed_events"])

    def test_malformed_schema_is_rejected(self) -> None:
        graph = {"version": 1, "nodes": [{"id": "a"}], "edges": "not-a-list"}
        trace = {"version": 1, "events": []}

        with self.assertRaisesRegex(InputError, "graph.edges must be a list"):
            audit(graph, trace)

    def test_mermaid_marks_missing_evidence_and_undeclared_edges_red(self) -> None:
        graph, trace = load_case("broken_edges")
        result = audit(graph, trace)

        output = render_mermaid(graph, result)

        self.assertIn("missing evidence", output)
        self.assertIn("undeclared transition", output)
        self.assertGreaterEqual(output.count("stroke:#d33"), 2)


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "graphcheck", *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_healthy_text_exit_zero(self) -> None:
        completed = self.run_cli(
            "examples/healthy/graph.json",
            "examples/healthy/trace.json",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("GraphCheck: PASS", completed.stdout)

    def test_cli_broken_json_exit_one(self) -> None:
        completed = self.run_cli(
            "examples/broken_edges/graph.json",
            "examples/broken_edges/trace.json",
            "--format",
            "json",
        )

        self.assertEqual(1, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("fail", payload["status"])

    def test_cli_malformed_json_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bad_graph = Path(temporary_directory) / "graph.json"
            bad_graph.write_text('{"version": 1,', encoding="utf-8")
            completed = self.run_cli(
                str(bad_graph),
                "examples/healthy/trace.json",
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn("invalid JSON", completed.stderr)


if __name__ == "__main__":
    unittest.main()
