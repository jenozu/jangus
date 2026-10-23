"""Reproducible offline toy benchmarks; not evidence of production savings."""

import json
import time

from jangus.integrations.advice.contracts import token_estimate
from jangus.integrations.graphify import plain_search, scan_python, search_graph
from jangus.integrations.headroom import compact, verify_reference


def run() -> dict:
    source = (
        b"def resolve():\n    return 1\n"
        b"def caller():\n    return resolve()\n"
        b"def unrelated():\n    return 0\n"
    )
    expected_names = {"resolve", "caller"}
    started = time.perf_counter()
    graph = scan_python(source, "fixture")
    found = {edge.source for edge in search_graph(graph, "resolve", source)}
    graph_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    baseline_hit = plain_search(source, "resolve")
    baseline_ms = (time.perf_counter() - started) * 1000
    context = (
        "Intro\n" * 8
        + "MUST preserve synthetic_job_id=abc123\n"
        + "Other\n" * 8
        + "ERROR verified_copy_conflict\n"
    )
    started = time.perf_counter()
    result = compact(
        context, reference="fixture:context", max_bytes=130,
        required_exact=("synthetic_job_id=abc123", "verified_copy_conflict"),
    )
    verify_reference(context, result)
    compact_ms = (time.perf_counter() - started) * 1000
    return {
        "provenance": "synthetic-only",
        "graph": {
            "revision": graph.revision,
            "edge_count": len(graph.edges),
            "exact_syntactic_names_retrieved": sorted(found & expected_names),
            "expected_names": sorted(expected_names),
            "plain_substring_hit": baseline_hit,
            "graph_elapsed_ms": graph_ms,
            "plain_elapsed_ms": baseline_ms,
        },
        "context": {
            "original_token_estimate": token_estimate(context),
            "compacted_token_estimate": result.excerpt_estimated_tokens,
            "truncated": result.truncated,
            "original_revision": result.original_sha256,
            "exact_constraints_preserved": True,
            "elapsed_ms": compact_ms,
        },
        "limitations": "Single synthetic fixture; no model calls, measured tokens, "
        "end-to-end accuracy, full retrieval accounting or live data.",
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, indent=2))
