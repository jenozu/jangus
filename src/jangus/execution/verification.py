"""Read actual outputs independently; tool return values never establish success."""

from jangus.execution.contracts import Action, RuntimeFault, digest
from jangus.execution.files import FileTools


def verify(files: FileTools, action: Action) -> dict:
    actual = files.read(action.path)
    value = digest(actual)
    if value != action.expected_sha256:
        raise RuntimeFault("verification_failed")
    return {"sha256": value, "bytes": len(actual)}
