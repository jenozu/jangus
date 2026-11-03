from pathlib import Path

import pytest

import jangus.agents.prompt as prompt_module
from jangus.agents.runner import Job
from jangus.kernel.cache import MAX_ENTRIES, MAX_VALUE_CHARS, CacheKey, ResultCache
from jangus.kernel.state import StateStore
from tests.agent_harness import Harness, make_spec, reply

READ = ("file.read", {"path": "/work/a"})


def cached(tmp_path: Path, clock: list[int] | None = None, **kw: object) -> Harness:
    now = clock if clock is not None else [1000]
    return Harness(tmp_path, use_cache=True, cache_clock=lambda: now[0], **kw)  # type: ignore[arg-type]


def two_replies() -> list[str]:
    return [reply(READ), reply(done=True)]


def job(job_id: str, instruction: str = "sort downloads", task_type: str = "sort") -> Job:
    return Job(job_id, instruction, inputs={"listing": "a.txt b.txt"}, task_type=task_type)


# ---- B41 ---------------------------------------------------------------------


def test_repeat_of_the_same_task_makes_zero_model_calls(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies()
    first = h.runner.run(job("j1"))
    assert first.model_calls == 2
    second = h.runner.run(job("j2"))  # same task type, input, spec and prompt version
    assert second.model_calls == 0 and second.done_claimed
    assert len(h.provider.requests) == 2  # no new request was sent
    assert len(h.events("cache_hit")) == 2
    assert len(h.events("model_call")) == 2  # a hit is not a model call: nothing metered


def test_a_hit_still_goes_through_the_kernel(tmp_path: Path) -> None:
    """The cached reply is data: its proposals are validated and executed again."""
    h = cached(tmp_path)
    h.provider.script += two_replies()
    h.runner.run(job("j1"))
    h.world.calls.clear()
    h.runner.run(job("j2"))
    assert h.world.calls == ["read:/work/a"]  # executed by the kernel, not skipped


def test_a_hit_costs_no_budget(tmp_path: Path) -> None:
    from jangus.kernel.budgets import Dimension

    h = cached(tmp_path)
    h.provider.script += two_replies()
    h.runner.run(job("j1"))
    spent = h.budgets.status("agent:file-agent")[Dimension.CALLS].spent
    h.runner.run(job("j2"))
    assert h.budgets.status("agent:file-agent")[Dimension.CALLS].spent == spent


def test_changing_the_input_misses(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies() + two_replies()
    h.runner.run(job("j1", "sort downloads"))
    again = h.runner.run(job("j2", "sort documents"))
    assert again.model_calls == 2


def test_changing_the_task_type_misses(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies() + two_replies()
    h.runner.run(job("j1", task_type="sort"))
    assert h.runner.run(job("j2", task_type="tidy")).model_calls == 2


def test_changing_the_spec_invalidates_the_entry(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies()
    h.runner.run(job("j1"))
    h.state.close()
    # the owner edits and re-approves the spec: its hash changes, same database files
    edited = cached(tmp_path, spec=make_spec(calls=21))
    assert edited.spec.sha256 != h.spec.sha256
    edited.provider.script += two_replies()
    assert edited.runner.run(job("j2")).model_calls == 2  # the old entry is never found


def test_changing_the_prompt_version_invalidates_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies() + two_replies()
    h.runner.run(job("j1"))
    monkeypatch.setattr("jangus.agents.runner.PROMPT_VERSION", "2")
    assert h.runner.run(job("j2")).model_calls == 2
    assert prompt_module.PROMPT_VERSION == "1"


def test_changing_the_model_misses(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies()
    h.runner.run(job("j1"))
    k1 = CacheKey("sort", "p", "ollama/a", "s", "1")
    k2 = CacheKey("sort", "p", "ollama/b", "s", "1")
    assert k1.digest() != k2.digest()


def test_ttl_expiry(tmp_path: Path) -> None:
    clock = [1000]
    h = cached(tmp_path, clock)
    h.provider.script += two_replies() + two_replies()
    h.runner.run(job("j1"))
    clock[0] += 3600
    assert h.runner.run(job("j2")).model_calls == 0  # inside the 24h TTL
    clock[0] += 24 * 3600
    assert h.runner.run(job("j3")).model_calls == 2  # expired: a real call again


def test_a_round_with_failures_is_not_cached(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += [reply(("nope.tool", {})), reply(done=True), reply(done=True)]
    h.runner.run(job("j1"))
    assert h.cache is not None and h.cache.size() == 1  # only the clean second round
    h.runner.run(job("j2"))  # the failed first round is called again, not replayed
    assert len(h.provider.requests) == 3


def test_an_unparseable_reply_is_never_cached(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += ["garbage", reply(done=True)]
    h.runner.run(job("j1"))
    assert h.cache is not None and h.cache.size() == 1


def test_a_stale_cached_reply_that_now_fails_is_dropped(tmp_path: Path) -> None:
    import dataclasses

    h = cached(tmp_path)
    h.provider.script += [reply(("file.write", {"path": "/work/a"}), done=True)]
    h.runner.run(job("j1"))
    assert h.cache is not None and h.cache.size() == 1

    def boom(args: object, secrets: object) -> dict[str, object]:
        raise OSError("disk went away")

    tool = h.registry._tools["file.write"]  # noqa: SLF001 - same name and schema, new behaviour
    h.registry._tools["file.write"] = dataclasses.replace(tool, execute=boom)  # type: ignore[arg-type]
    again = h.runner.run(job("j2"))
    assert again.executed[0].status == "unresolved" and again.model_calls == 0  # replayed, failed
    assert h.cache.size() == 0  # the stale entry was removed, not replayed forever


# ---- store limits ----------------------------------------------------------------


def key(n: int) -> CacheKey:
    return CacheKey("t", f"p{n}", "m", "s", "1")


def test_store_is_bounded_in_entries_and_size(tmp_path: Path) -> None:
    clock = [0]
    cache = ResultCache(StateStore.open(tmp_path / "s.db"), lambda: clock[0], ttl_seconds=10**9)
    assert cache.put(key(0), "x" * (MAX_VALUE_CHARS + 1)) is False
    for n in range(MAX_ENTRIES + 25):
        clock[0] = n
        cache.put(key(n), "v")
    assert cache.size() == MAX_ENTRIES
    assert cache.get(key(0)) is None and cache.get(key(MAX_ENTRIES + 24)) == "v"  # oldest dropped


def test_cache_survives_a_restart(tmp_path: Path) -> None:
    h = cached(tmp_path)
    h.provider.script += two_replies()
    h.runner.run(job("j1"))
    h.state.close()
    h2 = cached(tmp_path)
    assert h2.runner.run(job("j2")).model_calls == 0
    assert h2.provider.requests == []
