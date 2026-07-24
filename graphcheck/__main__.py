"""Command-line interface for ``python -m graphcheck``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .core import InputError, audit
from .render import render_mermaid, render_text


def _load_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read {label} file {path}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{label} file {path} is invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m graphcheck",
        description="Compare a declared graph with explicit transition events in a trace.",
    )
    parser.add_argument("graph", type=Path, help="declared graph JSON")
    parser.add_argument("trace", type=Path, help="execution trace JSON")
    parser.add_argument(
        "--format",
        choices=("text", "json", "mermaid"),
        default="text",
        help="output format (default: text)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        graph = _load_json(args.graph, "graph")
        trace = _load_json(args.trace, "trace")
        result = audit(graph, trace)
    except InputError as exc:
        print(f"GraphCheck input error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif args.format == "mermaid":
        print(render_mermaid(graph, result))
    else:
        print(render_text(result))
    return 1 if result.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
