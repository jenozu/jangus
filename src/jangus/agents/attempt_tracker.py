"""Connects the kernel's attempt counting to the run loop (the AttemptTracker seam).

Model choice comes only from here: the spec's default model, then (after the default
attempts have failed) the spec's escalation model once, then nothing. No reply, directive or
agent can pick a model, raise a limit or skip a rung.
"""

from collections.abc import Sequence

from jangus.agents.runner import ModelChoice
from jangus.agents.spec import LoadedSpec
from jangus.directives.models import Task
from jangus.kernel.alerts import AlertQueue
from jangus.kernel.attempts import Ladder, Step, TaskAttempts, TaskLimits
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore


class SpecAttemptTracker:
    def __init__(self, loaded: LoadedSpec, attempts: TaskAttempts) -> None:
        self._spec = loaded.spec
        self.attempts = attempts

    def choose_model(self) -> ModelChoice | None:
        step = self.attempts.step()
        spec = self._spec
        if step is Step.DEFAULT:
            m = spec.model
            return ModelChoice(m.provider, m.name, m.max_tokens, "specialist", "task")
        if step is Step.STEP_UP and spec.escalation_model is not None:
            e = spec.escalation_model
            return ModelChoice(e.provider, e.name, spec.model.max_tokens, "step-up", "step_up")
        return None

    def note_model_call(self) -> str | None:
        return self.attempts.note_model_call()

    def note_proposal(self, proposal_hash: str) -> str | None:
        return self.attempts.note_proposal(proposal_hash)

    def note_result(self, proposal_hash: str, result_hash: str | None) -> None:
        self.attempts.note_result(proposal_hash, result_hash)

    def note_elapsed_ms(self, elapsed_ms: int) -> str | None:
        return self.attempts.note_elapsed_ms(elapsed_ms)

    def end_round(self, failures: Sequence[str]) -> Sequence[str]:
        return self.attempts.end_round(failures).reasons

    def failure_reasons(self) -> Sequence[str]:
        return self.attempts.failure_reasons()


def limits_for(loaded: LoadedSpec, task: Task | None = None) -> TaskLimits:
    spec = loaded.spec
    return TaskLimits.narrowed(
        spec_model_calls=spec.attempts.max_model_calls,
        spec_proposals=spec.attempts.max_proposals,
        spec_wall_seconds=spec.budget.wall_seconds,
        task_model_calls=task.max_model_calls if task else None,
        task_proposals=task.max_proposals if task else None,
        task_wall_seconds=task.max_wall_seconds if task else None,
    )


def tracker_for(
    loaded: LoadedSpec,
    state: StateStore,
    ledger: Ledger,
    alerts: AlertQueue,
    key: str,
    task: Task | None = None,
) -> SpecAttemptTracker:
    spec = loaded.spec
    ladder = Ladder(
        default_model=spec.attempts.default_model,
        step_up=spec.attempts.step_up if spec.escalation_model is not None else 0,
    )
    return SpecAttemptTracker(
        loaded, TaskAttempts(state, ledger, alerts, key, limits_for(loaded, task), ladder)
    )
