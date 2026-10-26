"""Offline comparative fixtures; no provider calls or production performance claims."""

import hashlib
import json
import statistics
import time

from jangus.integrations.advice.contracts import token_estimate
from jangus.integrations.graphify import plain_search, scan_python, search_graph
from jangus.integrations.headroom import compact, verify_reference


def elapsed_ms(fn, repetitions: int = 25) -> float:
    values = []
    for _ in range(repetitions):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return statistics.median(values)


def run() -> dict:
    programs = [
        ("fixture_a", b"def resolve():\n    return 1\ndef caller():\n    return resolve()\n", "resolve", True),
        ("fixture_b", b"def actual():\n    return 1\ncomment = 'phantom()'\n", "phantom", False),
        ("fixture_c", b"import os\ndef reader():\n    return os.getcwd()\n", "reader", True),
    ]
    results = []
    for name, source, query, expected in programs:
        baseline = plain_search(source, query)
        graph = scan_python(source, name)
        found = bool(search_graph(graph, query, source))
        try:
            search_graph(graph, query, source + b"# changed\n")
            stale = False
        except ValueError:
            stale = True
        graph_duration = elapsed_ms(
            lambda source=source, name=name, query=query: search_graph(
                scan_python(source, name), query, source
            )
        )
        baseline_duration = elapsed_ms(
            lambda source=source, query=query: plain_search(source, query)
        )
        results.append(
            {
                "fixture": name,
                "expected_symbol": expected,
                "literal_search_hit": baseline,
                "literal_search_correct": baseline == expected,
                "graph_hit": found,
                "graph_correct": found == expected,
                "stale_revision_rejected": stale,
                "graph_build_and_search_median_ms": graph_duration,
                "literal_search_median_ms": baseline_duration,
                "edges": len(graph.edges),
                "revision_sha256": graph.revision,
            }
        )

    contexts = [
        (
            "bounded_ids",
            "ignore\n" * 16 + "MUST keep job_id=abc123\n"
            + "noise\n" * 16 + "ERROR conflict\n",
            120,
            ("job_id=abc123", "ERROR conflict"),
        ),
        (
            "bounded_paths",
            "noise\n" * 20 + "path=/tmp/example/report.txt\n"
            + "noise\n" * 12 + "hash=" + "a" * 64 + "\n",
            160,
            ("path=/tmp/example/report.txt", "a" * 64),
        ),
    ]
    compressed = []
    for name, source, budget, critical in contexts:
        reference = "fixture:" + name
        selected = compact(source, reference=reference, max_bytes=budget)
        verify_reference(source, selected)
        measure = elapsed_ms(
            lambda source=source, reference=reference, budget=budget: verify_reference(
                source, compact(source, reference=reference, max_bytes=budget)
            )
        )
        compressed.append(
            {
                "fixture": name,
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "critical_evidence_preserved": all(x in selected.excerpt for x in critical),
                "compaction_only_estimated_tokens": token_estimate(selected.excerpt),
                "full_context_estimated_tokens": token_estimate(source),
                "retrieval_plus_compaction_estimated_tokens": (
                    token_estimate(source) + token_estimate(selected.excerpt)
                ),
                "compaction_and_verify_median_ms": measure,
            }
        )
    return {
        "provenance": "synthetic-only",
        "graph": {
            "cases": results,
            "correct": sum(x["graph_correct"] for x in results),
            "ordinary_search_correct": sum(x["literal_search_correct"] for x in results),
        },
        "context": {
            "cases": compressed,
            "all_critical_preserved": all(x["critical_evidence_preserved"] for x in compressed),
        },
        "limitations": (
            "No provider calls or observed model tokens, task accuracy, end-to-end "
            "network latency or actual private data. Synthetic timings are illustrative."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, indent=2))
