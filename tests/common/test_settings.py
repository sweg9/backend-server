"""C-12: the settings files load into their models, and unknown device types are high risk."""

from backend.common.settings import EnclaveSettings, HubSettings, RiskSettings, ServerSettings, load, risk_of
from backend.common.vocab import DeviceType, Risk


def test_settings_files_load():
    load(HubSettings, "hub.yaml")
    load(ServerSettings, "server.yaml")
    load(RiskSettings, "risk.yaml")
    load(EnclaveSettings, "enclave.yaml")


def test_other_and_unlisted_types_are_high_risk():
    s = RiskSettings(risk={DeviceType.LIGHT: Risk.LOW})
    assert risk_of(s, DeviceType.LIGHT) == Risk.LOW
    assert risk_of(s, DeviceType.FAN) == Risk.HIGH
    assert risk_of(RiskSettings(risk={DeviceType.OTHER: Risk.LOW}), DeviceType.OTHER) == Risk.HIGH
