from jangus.errors import FaultCode, RuntimeFault


def test_fault_codes_are_stable_strings() -> None:
    assert FaultCode.CONFIG_INVALID.value == "config_invalid"
    assert FaultCode.ENDPOINT_NOT_LOOPBACK.value == "endpoint_not_loopback"


def test_runtime_fault_carries_code() -> None:
    fault = RuntimeFault(FaultCode.PATH_INVALID, "bad")
    assert fault.code is FaultCode.PATH_INVALID
    assert str(fault) == "path_invalid: bad"
