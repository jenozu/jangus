"""Opt-in approved local memory for isolated agent roles.

All memory is lower-trust reference data, never a rule or tool permission.
"""

from dataclasses import dataclass

from jangus.logs import get
from jangus.memory.proposals import Proposal, propose_lesson
from jangus.memory.registry import ApprovalRegistry
from jangus.memory.retrieval import render_for_prompt, select
from jangus.memory.schema import MemoryFault, Vault

_LOG = get("memory")


@dataclass(frozen=True)
class MemoryContext:
    vault: Vault
    registry: ApprovalRegistry
    scope: str
    query: str
    required: bool = False
    propose_on_success: bool = False
    max_bytes: int = 4096

    def read_for_role(self, *, run_id: str) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Select and revalidate at EACH role prompt; only local delivery."""
        choices = select(
            self.vault,
            self.registry,
            query=self.query,
            scope=self.scope,
            destination="local",
            limit=2,
            required=self.required,
        )
        if not choices:
            if self.required:
                raise MemoryFault("memory_required_unavailable")
            return "", ()
        try:
            text = render_for_prompt(
                self.vault,
                self.registry,
                choices,
                scope=self.scope,
                destination="local",
                evidence="run:" + run_id,
                max_bytes=self.max_bytes,
            )
        except MemoryFault as exc:
            if self.required:
                raise
            _LOG.warning("memory render skipped: %s", exc)
            return "", ()
        return text, tuple((choice.note_id, choice.sha256) for choice in choices)

    def draft_from_verified_run(self, *, run_id: str, action_count: int) -> Proposal:
        """Explicit opt-in deterministic proposal; never an approval."""
        if not self.propose_on_success or not 1 <= action_count <= 100:
            raise MemoryFault("proposal_not_authorized")
        return propose_lesson(
            self.vault,
            scope=self.scope,
            summary=(
                "A synthetic file workflow completed with independent output verification "
                f"for {action_count} authorized actions. Review the original run evidence "
                "before treating this as a reusable procedure."
            ),
            evidence="run:" + run_id,
        )
