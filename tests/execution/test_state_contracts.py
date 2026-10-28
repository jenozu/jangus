"""Typed state vocabularies must match the database constraints exactly."""

import re
import typing

from jangus.execution.contracts import ActionStatus, RunStatus
from jangus.execution.storage import DDL, TERMINAL


def _check_values(table: str) -> set[str]:
    statement = next(item for item in DDL if f"CREATE TABLE {table} " in item)
    match = re.search(r"state (?:TEXT NOT NULL )?CHECK\(state IN\s*\(([^)]*)\)", statement)
    assert match, table
    return set(re.findall(r"'([a-z]+)'", match.group(1)))


def test_literals_match_sql_check_constraints():
    assert set(typing.get_args(RunStatus)) == _check_values("runs")
    assert set(typing.get_args(ActionStatus)) == _check_values("actions")


def test_terminal_states_are_run_states():
    assert TERMINAL <= set(typing.get_args(RunStatus))
