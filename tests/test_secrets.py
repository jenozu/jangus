import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.executor import AgentContext, Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Reconciliation, Registry, Tier, ToolSpec
from jangus.kernel.secrets import (
    EnvBackend,
    MemoryBackend,
    WindowsCredentialBackend,
    resolve_secrets,
)
from jangus.kernel.state import StateStore
from tests.fakes import PathArgs

CANARY = "CANARY-SECRET-9f3a7c"


class Seen:
    got: str | None = None


def build(seen: Seen, mode: str = "ok") -> Registry:
    reg = Registry()

    def call(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        seen.got = secrets.get("shop_token")
        if mode == "raise":
            raise RuntimeError(f"auth failed with {secrets['shop_token']}")
        if mode == "echo":
            return {"debug": f"token={secrets['shop_token']}", "ok": True}
        return {"ok": True}

    reg.register(
        ToolSpec(
            "shop.read",
            1,
            PathArgs,
            Tier.T2,
            True,
            call,
            verify=lambda a, r: True,
            reconcile=lambda a: Reconciliation("absent"),
            secrets=("shop_token",),
        )
    )
    return reg


def rig(tmp_path: Path, registry: Registry, backend: Any = None):  # type: ignore[no-untyped-def]
    state = StateStore.open(tmp_path / "state.db")
    state.set_budget("calls", 10)
    run_id = state.create_run("job", "shop").id
    ledger = Ledger.open(tmp_path / "ledger.jsonl", clock=lambda: "t")
    backend = MemoryBackend({"shop_token": CANARY}) if backend is None else backend
    return state, ledger, run_id, Executor(registry, state, ledger, secrets=backend)


def everything_on_disk(tmp_path: Path) -> bytes:
    return b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())


# B07
def test_agent_without_secret_in_spec_is_denied_before_any_effect(tmp_path: Path) -> None:
    seen = Seen()
    state, ledger, run_id, ex = rig(tmp_path, build(seen))
    agent = AgentContext({"shop.read"}, Tier.T2, "calls", secrets_allowed=frozenset())
    r = ex.submit(run_id, agent, "shop.read", {"path": "/x"})
    assert r.status == "rejected"
    assert seen.got is None  # the tool never ran, so it never saw the secret
    assert ledger.entries()[0].event["reason"] == "secret_denied"
    assert state.list_open_actions() == []
    assert state.get_budget("calls").spent == 0
    assert CANARY.encode() not in everything_on_disk(tmp_path)


def test_agent_with_secret_in_spec_gets_it_injected(tmp_path: Path) -> None:
    seen = Seen()
    _, _, run_id, ex = rig(tmp_path, build(seen))
    agent = AgentContext({"shop.read"}, Tier.T2, "calls", secrets_allowed=frozenset({"shop_token"}))
    assert ex.submit(run_id, agent, "shop.read", {"path": "/x"}).status == "done"
    assert seen.got == CANARY


@pytest.mark.parametrize("mode", ["ok", "raise", "echo"])
def test_canary_never_reaches_ledger_state_results_or_errors(tmp_path: Path, mode: str) -> None:
    seen = Seen()
    state, ledger, run_id, ex = rig(tmp_path, build(seen, mode))
    agent = AgentContext({"shop.read"}, Tier.T2, "calls", secrets_allowed=frozenset({"shop_token"}))
    r = ex.submit(run_id, agent, "shop.read", {"path": "/x"})
    assert seen.got == CANARY  # the tool really did receive it
    assert CANARY not in repr(r)
    assert CANARY.encode() not in everything_on_disk(tmp_path)
    state.close()
    assert CANARY.encode() not in everything_on_disk(tmp_path)  # incl. WAL contents after close
    if mode == "echo":
        assert r.status == "done" and r.result == {"debug": "token=[REDACTED]", "ok": True}
        assert ledger.entries()[-1].event["note"] == "verified_redacted"
    if mode == "raise":
        assert r.status == "unresolved"


def test_missing_secret_and_missing_backend_fail_closed(tmp_path: Path) -> None:
    tool = build(Seen()).get("shop.read")
    assert tool is not None
    for backend in (MemoryBackend({}), None):
        with pytest.raises(RuntimeFault) as info:
            resolve_secrets(backend, tool, {"shop_token"})
        assert info.value.code in {FaultCode.SECRET_MISSING, FaultCode.SECRET_BACKEND_UNAVAILABLE}
    with pytest.raises(RuntimeFault) as denied:
        resolve_secrets(MemoryBackend({"shop_token": CANARY}), tool, set())
    assert denied.value.code is FaultCode.SECRET_DENIED
    assert CANARY not in str(denied.value)


def test_env_backend_reads_prefixed_variable() -> None:
    backend = EnvBackend({"JANGUS_SECRET_SHOP_TOKEN": "v"})
    assert backend.get("shop_token") == "v"
    assert backend.get("other") is None


def test_windows_backend_fails_closed_without_keyring() -> None:
    try:
        import keyring  # type: ignore[import-not-found,unused-ignore]  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeFault) as info:
            WindowsCredentialBackend().get("x")
        assert info.value.code is FaultCode.SECRET_BACKEND_UNAVAILABLE
    else:
        pytest.skip("keyring installed here; the real store is an owner-run Windows test")


def test_recovery_of_tool_needing_secrets_fails_closed_without_allow_list(tmp_path: Path) -> None:
    class Crash(BaseException):
        pass

    def die(name: str) -> None:
        if name == "after_intent_ledger":
            raise Crash

    seen = Seen()
    reg = build(seen)
    state, ledger, run_id, _ = rig(tmp_path, reg)
    ex = Executor(reg, state, ledger, secrets=MemoryBackend({"shop_token": CANARY}), checkpoint=die)
    agent = AgentContext({"shop.read"}, Tier.T2, "calls", secrets_allowed=frozenset({"shop_token"}))
    with pytest.raises(Crash):
        ex.submit(run_id, agent, "shop.read", {"path": "/x"}, key="k")
    state.close()
    state2 = StateStore.open(tmp_path / "state.db")
    ledger2 = Ledger.open(tmp_path / "ledger.jsonl", clock=lambda: "t")
    ex2 = Executor(reg, state2, ledger2, secrets=MemoryBackend({"shop_token": CANARY}))
    assert [s.status for s in ex2.recover()] == ["unresolved"]
    assert seen.got is None
    action = state2.get_action("k")
    assert action is not None and action.status == "unresolved"
    sqlite3.connect(tmp_path / "state.db").close()
