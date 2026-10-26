"""Phase 8 hardening regressions, with no network or model access."""

import hashlib
import json

import pytest

from jangus.integrations.advice.contracts import AdviceFault, Evidence, pack_evidence
from jangus.integrations.advice.providers import AstraTextOnly
from jangus.integrations.graphify import scan_python, search_graph
from jangus.integrations.headroom import compact, verify_reference


def test_external_evidence_revision_cannot_be_mutated_after_validation():
    evidence = Evidence(
        reference="case:fixed",
        text="synthetic",
        sha256=hashlib.sha256(b"synthetic").hexdigest(),
        sharing="external-ok",
        redacted=True,
    )
    with pytest.raises(ValueError, match="evidence revision changed"):
        pack_evidence([evidence.model_copy(update={"text": "private-change"})])
    with pytest.raises(AdviceFault, match="disclosure_denied"):
        pack_evidence([evidence.model_copy(update={"sharing": "local"})])


def test_graph_rejects_revision_drift_and_avoids_string_false_positives():
    source = (
        b"def real():\n    return 1\n"
        b"def caller():\n    return real()\n"
        b"statement = 'phantom_function() appears only in string'\n"
    )
    graph = scan_python(source, "fixture")
    assert not search_graph(graph, "phantom_function", source)
    assert {edge.relation for edge in search_graph(graph, "real", source)} == {
        "declares", "calls-name"
    }
    with pytest.raises(ValueError, match="graph_stale"):
        search_graph(graph, "real", source + b"# changed\n")


def test_compaction_preserves_ids_hashes_paths_and_errors_automatically():
    full_hash = "a" * 64
    source = (
        "unused intro\n" * 10
        + "job_id=abc123\n"
        + "trace /srv/app/out.txt\n"
        + "check sha256=" + full_hash + "\n"
        + "ERROR result_uncertain\n"
        + "irrelevant trace\n" * 10
    )
    kept = compact(source, reference="synthetic:evidence", max_bytes=215)
    assert kept.truncated
    assert "job_id=abc123" in kept.excerpt
    assert "/srv/app/out.txt" in kept.excerpt
    assert full_hash in kept.excerpt
    assert "ERROR result_uncertain" in kept.excerpt
    verify_reference(source, kept)
    with pytest.raises(ValueError, match="required_evidence_exceeds_budget"):
        compact(source, reference="synthetic:evidence", max_bytes=32)


def test_compaction_refuses_unavailable_original_or_tampered_excerpt():
    source = "MUST keep scope\nnoise\n"
    selected = compact(source, reference="fixture:source", max_bytes=16)
    verify_reference(source, selected)
    with pytest.raises(ValueError, match="original_evidence_changed"):
        verify_reference(source + "changed", selected)
    with pytest.raises(ValueError, match="context_excerpt_changed"):
        verify_reference(
            source,
            selected.__class__(
                excerpt="MUST remove scope\n",
                original_sha256=selected.original_sha256,
                reference=selected.reference,
                original_estimated_tokens=selected.original_estimated_tokens,
                excerpt_estimated_tokens=selected.excerpt_estimated_tokens,
                truncated=selected.truncated,
            ),
        )


class _FakeReply:
    status = 200
    def __init__(self, raw):
        self.raw = raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.raw[:limit]


class _FakeTransport:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return _FakeReply(self.raw)


@pytest.mark.parametrize("invalid", [
    b"not-json",
    b'{"status":"incomplete","output":[]}',
    b'{"status":"completed","output":[]}',
    b'{"status":"completed","output":[{"type":"tool_call","content":[]}]}',
    b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"ok"},{"type":"output_text","text":"more"}]}]}',
])
def test_astra_does_not_accept_invalid_or_tool_outputs(invalid):
    from jangus.integrations.advice.providers import AdviceFault as ProviderFault

    transport = _FakeTransport(invalid)
    adapter = AstraTextOnly("synthetic-local-fake-key-do-not-use", opener=transport)
    with pytest.raises(ProviderFault, match="provider_response_invalid"):
        adapter("synthetic redacted", 4, 512)
    assert len(transport.calls) == 1
    args = json.loads(transport.calls[0][0].data)
    assert args["tools"] == [] and args["store"] is False
