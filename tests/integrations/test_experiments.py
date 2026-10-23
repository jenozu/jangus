"""Offline baseline-only Graphify and Headroom experiments."""

import hashlib
import json

import pytest

from jangus.integrations.advice.providers import AstraTextOnly
from jangus.integrations.graphify import plain_search, scan_python, search_graph
from jangus.integrations.headroom import compact, verify_reference


def test_ast_index_edges_are_syntactic_not_semantically_inferred():
    raw = b"import json\ndef helper():\n    return 1\ndef caller():\n    return helper()\n"
    graph = scan_python(raw, "synthetic")
    assert graph.revision == hashlib.sha256(raw).hexdigest()
    assert {e.relation for e in search_graph(graph, "helper", raw)} == {"declares", "calls-name"}
    assert plain_search(raw, "helper")
    with pytest.raises(ValueError, match="graph_stale"):
        search_graph(graph, "helper", raw + b"\n")
    with pytest.raises(ValueError, match="invalid_query"):
        search_graph(graph, "../secret", raw)


def test_preserve_constraints_and_retrieve_original_by_digest():
    raw = (
        "A. Avoid repeated output\n"
        "MUST keep request job_id=123 and attempt=2\n"
        "Log line 1\nLog line 2\nLog line 3\n"
        "ERROR invalid_digest\n"
        "Final note\n"
    )
    result = compact(
        raw, reference="evidence:1", max_bytes=92, required_exact=("job_id=123", "invalid_digest")
    )
    assert result.truncated
    assert "job_id=123" in result.excerpt
    assert "invalid_digest" in result.excerpt
    assert result.original_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert result.excerpt_estimated_tokens <= result.original_estimated_tokens
    verify_reference(raw, result)
    with pytest.raises(ValueError, match="original_evidence_changed"):
        verify_reference(raw + "updated", result)
    with pytest.raises(ValueError, match="required_evidence_exceeds_budget"):
        compact(raw, reference="evidence:1", max_bytes=12, required_exact=("job_id=123",))


def test_broker_is_not_required_for_offline_experiments():
    graph = scan_python(b"def f():\n    pass\n", "example")
    assert graph.edges[0].target == "f"
    data = compact("Exact source\n", reference="fixture:1", max_bytes=50)
    assert not data.truncated


class _Response:
    status = 200

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.content[:limit]


class _Opener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response(
            json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": '{"summary":"ok","action_ids":[]}'}
                            ],
                        }
                    ],
                }
            ).encode()
        )



def test_astra_prototype_uses_no_tools_and_no_storage():
    opener = _Opener()
    model = AstraTextOnly("synthetic-example-key-length-32", opener=opener)
    assert json.loads(model("redacted synthetic data", 8, 1024))["summary"] == "ok"
    request, seconds = opener.requests[0]
    body = json.loads(request.data)
    assert seconds == 8
    assert body["model"] == "gpt-6-astra"
    assert body["tools"] == []
    assert body["tool_choice"] == "none"
    assert body["store"] is False
    assert body["input"] == "redacted synthetic data"
