from datetime import UTC, datetime
from pathlib import Path

import pytest

from jangus.agents.context import (
    ContextTooLarge,
    ResultItem,
    fit_user_prompt,
    shrink,
    summarize_result,
    trim_text,
)
from jangus.agents.metering import estimate_tokens, usage_by_role
from jangus.agents.pricing import PriceTable
from jangus.agents.providers.base import Usage
from jangus.agents.runner import Job, RunOutcome
from jangus.kernel.alerts import read_alerts
from tests.agent_harness import Harness, make_spec, reply


def item(i: int, size: int = 50) -> ResultItem:
    return ResultItem(i, "file.read", {"status": "done", "result": {"names": ["f" * 20] * size}})


# ---- deterministic shrinking -----------------------------------------------------


def test_summaries_are_deterministic_and_bounded() -> None:
    big = {"files": [f"file-{i}.txt" for i in range(1000)], "note": "x" * 5000}
    a, b = summarize_result(big, 600), summarize_result(big, 600)
    assert a == b and len(a) <= 600
    assert '"_list_of": 1000' in a  # the count survives
    assert "file-0.txt" in a  # and the first items


def test_small_values_are_left_alone() -> None:
    value = {"path": "/work/a", "size": 3, "ok": True, "none": None}
    assert (
        summarize_result(value, 4000) == '{"none": null, "ok": true, "path": "/work/a", "size": 3}'
    )


def test_deep_and_wide_structures_are_cut() -> None:
    wide = {f"k{i:02d}": i for i in range(50)}
    out = shrink(wide, items=3, chars=10)
    assert out["_more_keys"] == 30 and len(out) == 21
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    assert "_keys" in str(shrink(deep, items=3, chars=10))


def test_trim_text_keeps_head_and_tail() -> None:
    text = "H" * 500 + "M" * 5000 + "T" * 500
    out = trim_text(text, 1000)
    assert out.startswith("HHH") and out.endswith("TTT") and "kernel trimmed" in out
    assert len(out) <= 1000 and trim_text("short", 1000) == "short"


# ---- fitting the prompt ----------------------------------------------------------------


def fit(
    limit: int | None, inputs: dict[str, str] | None = None, n_results: int = 0
) -> tuple[str, bool]:
    return fit_user_prompt(
        system="S" * 300,
        instruction="do the task",
        inputs=inputs or {},
        results=[item(i) for i in range(1, n_results + 1)],
        failures=[],
        max_input_tokens=limit,
    )


def test_a_prompt_that_fits_is_not_touched() -> None:
    text, trimmed = fit(10_000, {"note": "small"})
    assert not trimmed and "small" in text
    assert fit(None, {"note": "any size"})[1] is False  # no limit, no trimming


def test_older_results_are_collapsed_before_anything_else() -> None:
    full, _ = fit(None, n_results=8)
    text, trimmed = fit(estimate_tokens("S" * 300 + full) // 2, n_results=8)
    assert trimmed and "details dropped by kernel" in text
    assert 'label="result-8-file.read"' in text  # the newest results stay in full


def test_large_inputs_are_trimmed_to_fit() -> None:
    text, trimmed = fit(1500, {"mail": "m" * 40_000, "log": "l" * 40_000})
    assert trimmed and estimate_tokens("S" * 300 + text) <= 1500
    assert text.count("kernel trimmed") == 2


def test_prompt_that_cannot_fit_is_refused() -> None:
    with pytest.raises(ContextTooLarge):
        fit(40, {"mail": "m" * 10_000})
    with pytest.raises(ContextTooLarge):
        fit(40)  # even the bare task text does not fit


# ---- in the run loop ----------------------------------------------------------------------


def test_trimmed_input_is_sent_within_the_limit_and_recorded(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_input_tokens=2000))
    h.provider.script.append(reply(done=True))
    h.runner.run(Job("j1", "summarize", inputs={"mail": "m" * 50_000}))
    sent = h.provider.requests[0]
    assert estimate_tokens(sent.system + sent.user) <= 2000
    assert h.events("context_trimmed")


def test_call_that_cannot_fit_is_refused_counted_and_not_sent(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_input_tokens=100))
    h.provider.script.append(reply(done=True))
    report = h.runner.run(Job("j1", "summarize"))
    assert report.outcome is RunOutcome.REFUSED and "context_too_large" in report.failures
    assert h.provider.requests == [] and h.escalation.requests == []
    assert any(a.code == "context_too_large" for a in read_alerts(h.ledger))


def test_a_task_can_tighten_the_limit_but_not_loosen_it(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_input_tokens=2000))
    assert h.runner._input_limit(Job("j", "x", max_input_tokens=500)) == 500  # noqa: SLF001
    assert h.runner._input_limit(Job("j", "x", max_input_tokens=9000)) == 2000  # noqa: SLF001
    assert h.runner._input_limit(Job("j", "x")) == 2000  # noqa: SLF001


def test_directive_task_limit_reaches_the_run_loop(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_input_tokens=5000, escalation=False))
    task = {
        "id": 1,
        "title": "big",
        "testStrategy": "x",
        "tools": ["file.read"],
        "max_input_tokens": 120,
    }
    d = h.issue_directive([task])
    report = h.directives.run(d, verifiers={1: lambda t, e: True})
    assert report.halted is not None and h.provider.requests == []
    assert h.events("attempt_failed")[0]["reasons"] == ["context_too_large"]


def test_long_tool_result_goes_back_as_a_short_summary(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.world.files["/work/big"] = "data"
    h.provider.script += [reply(("file.read", {"path": "/work/big"})), reply(done=True)]
    h.world.files["/work/big"] = "z" * 100_000  # a very large file body
    h.runner.run(Job("j1", "read it"))
    second = h.provider.requests[1].user
    assert len(second) < 3000 and "chars)" in second  # cut by the kernel, with a count


# ---- provider savings --------------------------------------------------------------------------


def test_system_prefix_is_stable_and_marked_cacheable(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.provider.script += [reply(done=True), reply(done=True)]
    h.runner.run(Job("j1", "first task", inputs={"a": "1"}))
    h.runner.run(Job("j2", "a different task", inputs={"b": "2"}))
    r1, r2 = h.provider.requests
    assert r1.system == r2.system and r1.user != r2.user  # the cacheable part never varies
    assert r1.cache_prefix and r2.cache_prefix


def test_cached_tokens_are_recorded_and_priced_at_the_cached_rate(tmp_path: Path) -> None:
    prices = PriceTable.parse(
        {
            "as_of": datetime.now(UTC).date(),
            "models": {
                "claude/opus-tag": {
                    "input_per_mtok": 10,
                    "output_per_mtok": 50,
                    "cached_per_mtok": 1,
                }
            },
        }
    )
    h = Harness(
        tmp_path,
        spec=make_spec(provider="claude", model="opus-tag", escalation=False),
        prices=prices,
    )
    h.escalation.usage = Usage(input_tokens=100, output_tokens=20, cached_tokens=900)
    h.escalation.script.append(reply(done=True))
    h.runner.run(Job("j1", "x"))
    call = h.events("model_call")[0]
    assert call["cached_tokens"] == 900
    assert call["cost_usd_micros"] == 100 * 10 + 20 * 50 + 900 * 1  # cached tokens at $1 not $10
    assert usage_by_role(h.ledger)["specialist"]["cached_tokens"] == 900
