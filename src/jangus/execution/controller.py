"""Single-executor deterministic workflow with durable intent and reconciliation."""

import json
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from jangus.config import JangusConfig, _external_path
from jangus.execution.contracts import (
    MAX_MANIFEST_BYTES,
    POLICY_VERSION,
    Action,
    Manifest,
    RuntimeFault,
    compile_plan,
    digest,
    encoded,
)
from jangus.execution.files import FileTools, no_links
from jangus.execution.storage import TERMINAL, Store
from jangus.execution.verification import verify
from jangus.paths import checkout_roots

# Reviewed root rules.md SHA-256, recorded with every run; no model loading implied.
RULES_HASH = "1fee072c80db31cb7c7757fcc5ba3598384b2c3903b14f2ae6c8fb72c6b9ad22"


def read_manifest(path: Path) -> Manifest:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RuntimeFault("manifest_too_large")

        # Reject duplicate object keys instead of allowing silent replacement.
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = value
            return result

        return Manifest.model_validate(json.loads(raw, object_pairs_hook=unique))
    except (OSError, ValueError, ValidationError, RecursionError):
        raise RuntimeFault("invalid_manifest") from None


class Controller:
    def __init__(self, config: JangusConfig):
        self.config = config
        roots = checkout_roots()
        for value in (config.paths.data_dir, config.paths.workspace_dir):
            no_links(Path(value).expanduser().absolute())
        self.workspace_root = _external_path(config.paths.workspace_dir, roots)
        data = _external_path(config.paths.data_dir, roots)
        if data.is_relative_to(self.workspace_root) or self.workspace_root.is_relative_to(data):
            raise RuntimeFault("overlapping_storage")
        self.store = Store(data, config.logging.max_total_mb * 1024 * 1024)
        self.config_hash = digest(encoded(config.model_dump()))

    def close(self):
        self.store.close()

    def files(self, workspace: Path) -> FileTools:
        if not workspace.is_absolute():
            raise RuntimeFault("unsafe_path")
        no_links(workspace)
        resolved = workspace.resolve()
        if resolved == self.workspace_root or not resolved.is_relative_to(self.workspace_root):
            raise RuntimeFault("unsafe_path")
        return FileTools(resolved, set(self.config.permissions.allowed_tools))

    def submit(self, workspace: Path, manifest: Manifest) -> str:
        plan = compile_plan(manifest)
        if len(plan.actions) > self.config.limits.max_actions:
            raise RuntimeFault("budget_exhausted")
        files = self.files(workspace)
        required = {a.tool for a in plan.actions}
        if not required <= files.allowed:
            raise RuntimeFault("tool_denied")
        with self.store.lock():
            for action in plan.actions:
                if files.path(action.path).exists():
                    raise RuntimeFault("output_conflict")
                if action.source and digest(files.read(action.source)) != action.expected_sha256:
                    raise RuntimeFault("input_mismatch")
            run_id, now = str(uuid.uuid4()), time.time()
            identity = encoded([files.identity.st_dev, files.identity.st_ino]).decode()
            with self.store.transaction():
                self.store.db.execute(
                    """INSERT INTO runs
                    (id,state,created,updated,deadline,config_hash,policy_version,rules_hash,
                     workspace,workspace_identity,manifest,plan,revision,max_attempts,max_dispatches)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        "queued",
                        now,
                        now,
                        now + self.config.limits.run_timeout_seconds,
                        self.config_hash,
                        POLICY_VERSION,
                        RULES_HASH,
                        str(files.workspace),
                        identity,
                        manifest.model_dump_json(),
                        plan.model_dump_json(),
                        plan.revision,
                        self.config.limits.max_attempts_per_task,
                        self.config.limits.max_actions,
                    ),
                )
                for ordinal, action in enumerate(plan.actions):
                    self.store.db.execute(
                        """INSERT INTO actions
                        (run_id,id,ordinal,definition,state) VALUES (?,?,?,?,?)""",
                        (run_id, action.id, ordinal, action.model_dump_json(), "pending"),
                    )
                self.store.event(run_id, "submitted")
        return run_id

    def status(self, run_id: str) -> dict:
        run = self.store.run(run_id)
        actions = self.store.actions(run_id)
        return {
            "run_id": run_id,
            "state": run["state"],
            "revision": run["revision"],
            "cancel_requested": bool(run["cancelled"]),
            "error": run["error"],
            "dispatches": run["dispatches"],
            "max_dispatches": run["max_dispatches"],
            "deadline": run["deadline"],
            "policy_version": run["policy_version"],
            "config_sha256": run["config_hash"],
            "rules_sha256": run["rules_hash"],
            "actions": [
                {k: a[k] for k in ("id", "state", "attempts", "error")}
                | {
                    "evidence": json.loads(a["evidence"]) if a["evidence"] else None,
                    "remaining_attempts": max(0, run["max_attempts"] - a["attempts"]),
                }
                for a in actions
            ],
        }

    def _finish(self, run_id, state, code=None):
        with self.store.transaction():
            if state == "succeeded" and self.store.run(run_id)["cancelled"]:
                state, code = "cancelled", "cancelled"
            self.store.transition(run_id, state, code)
            self.store.event(run_id, state, code=code or "ok")

    def _verified(self, run_id, row, evidence, reconciled=False):
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE actions SET state=?,evidence=?,error=NULL WHERE run_id=? AND id=?",
                ("verified", encoded(evidence).decode(), run_id, row["id"]),
            )
            self.store.event(run_id, "reconciled" if reconciled else "verified", row, **evidence)

    def _reconcile(self, run_id, files):
        """Adopt only matching bytes from a persisted intent; never delete effects."""
        for row in self.store.actions(run_id):
            if row["state"] not in {"intent", "uncertain"}:
                continue
            action = Action.model_validate_json(row["definition"])
            try:
                evidence = verify(files, action)
            except FileNotFoundError:
                with self.store.transaction():
                    self.store.db.execute(
                        "UPDATE actions SET state=?,error=? WHERE run_id=? AND id=?",
                        ("pending", "interrupted_absent", run_id, row["id"]),
                    )
                    self.store.event(run_id, "reconciled", row, code="interrupted_absent")
            except (RuntimeFault, OSError):
                with self.store.transaction():
                    self.store.db.execute(
                        "UPDATE actions SET state=?,error=? WHERE run_id=? AND id=?",
                        ("uncertain", "verification_failed", run_id, row["id"]),
                    )
                    self.store.event(run_id, "uncertain", row, code="verification_failed")
            else:
                self._verified(run_id, row, evidence, reconciled=True)

    def execute(self, run_id: str, *, revision: int = 1, hook=None) -> dict:
        """hook is an in-process fault-injection seam, never a CLI/config option."""
        hook = hook or (lambda _stage, _action: None)
        with self.store.lock():
            run = self.store.run(run_id)
            if type(revision) is not int or revision != run["revision"]:
                raise RuntimeFault("stale_plan")
            if (
                run["config_hash"] != self.config_hash
                or run["policy_version"] != POLICY_VERSION
                or run["rules_hash"] != RULES_HASH
            ):
                raise RuntimeFault("policy_changed")
            if run["state"] in TERMINAL:
                return self.status(run_id)
            files = self.files(Path(run["workspace"]))
            identity = [files.identity.st_dev, files.identity.st_ino]
            if identity != json.loads(run["workspace_identity"]):
                raise RuntimeFault("workspace_changed")
            manifest = Manifest.model_validate_json(run["manifest"])
            with self.store.transaction():
                self.store.transition(run_id, "running")
                self.store.event(run_id, "started")
            self._reconcile(run_id, files)
            while True:
                run, rows = self.store.run(run_id), self.store.actions(run_id)
                if run["cancelled"]:
                    self._finish(run_id, "cancelled", "cancelled")
                    break
                if any(r["state"] == "uncertain" for r in rows):
                    with self.store.transaction():
                        self.store.transition(run_id, "waiting", "verification_failed")
                    break
                if all(r["state"] == "verified" for r in rows):
                    # Final independent sweep catches changes to earlier outputs.
                    try:
                        for row in rows:
                            verify(files, Action.model_validate_json(row["definition"]))
                    except (RuntimeFault, OSError):
                        self._finish(run_id, "failed", "verification_failed")
                    else:
                        self._finish(run_id, "succeeded")
                    break
                now = time.time()
                if now > run["deadline"] or now < run["created"]:
                    self._finish(run_id, "failed", "deadline")
                    break
                done = {r["id"] for r in rows if r["state"] == "verified"}
                row = next(
                    (
                        r
                        for r in rows
                        if r["state"] == "pending"
                        and set(json.loads(r["definition"])["dependencies"]) <= done
                    ),
                    None,
                )
                if row is None:
                    self._finish(run_id, "failed", "verification_failed")
                    break
                if (
                    row["attempts"] >= run["max_attempts"]
                    or run["dispatches"] >= run["max_dispatches"]
                ):
                    self._finish(run_id, "failed", "budget_exhausted")
                    break
                action = Action.model_validate_json(row["definition"])
                # Outputs present before a fresh intent belong to someone else,
                # even when their bytes happen to match this assignment.
                try:
                    conflict = files.path(action.path).exists()
                except (RuntimeFault, OSError):
                    self._finish(run_id, "failed", "unsafe_path")
                    break
                if conflict:
                    self._finish(run_id, "failed", "output_conflict")
                    break
                with self.store.transaction():
                    if self.store.run(run_id)["cancelled"]:
                        continue
                    self.store.db.execute(
                        "UPDATE actions SET state=?,attempts=attempts+1 WHERE run_id=? AND id=?",
                        ("intent", run_id, row["id"]),
                    )
                    self.store.db.execute(
                        "UPDATE runs SET dispatches=dispatches+1 WHERE id=?", (run_id,)
                    )
                    row["attempts"] += 1
                    self.store.event(run_id, "intent", row)
                hook("after_intent", action)
                if self.store.run(run_id)["cancelled"]:
                    self._reconcile(run_id, files)
                    continue
                try:
                    hook("before_effect", action)
                    files.execute(action, manifest.report_bytes())
                    hook("after_effect", action)
                except (RuntimeFault, OSError) as exc:
                    # Inspect actual artifacts before deciding whether retry is safe.
                    self._reconcile(run_id, files)
                    current = next(r for r in self.store.actions(run_id) if r["id"] == row["id"])
                    if current["state"] != "pending":
                        continue
                    if isinstance(exc, RuntimeFault):
                        self._finish(run_id, "failed", exc.code)
                        break
                    with self.store.transaction():
                        self.store.event(run_id, "retry", row, code="io_failure")
                    continue
                with self.store.transaction():
                    self.store.event(run_id, "result", row, code="ok")
                hook("before_verification", action)
                try:
                    evidence = verify(files, action)
                except (RuntimeFault, OSError):
                    with self.store.transaction():
                        self.store.db.execute(
                            "UPDATE actions SET state=?,error=? WHERE run_id=? AND id=?",
                            ("failed", "verification_failed", run_id, row["id"]),
                        )
                    self._finish(run_id, "failed", "verification_failed")
                    break
                self._verified(run_id, row, evidence)
                hook("after_verification", action)
            return self.status(run_id)
