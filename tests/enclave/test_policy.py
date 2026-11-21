"""FR-06: the autonomy policy in the enclave, as the wiki's table says (Introduction > Autonomy by risk)."""

import pytest

from backend.common.messages import Decide
from backend.common.settings import RiskSettings, load
from backend.common.vocab import Decision
from backend.enclave.policy import decide

S = load(RiskSettings, "risk.yaml")


def req(device_type="light", risk="low", action="on", confidence=0.9, status="Verified", independence=None):
    return Decide.model_validate(
        {
            "trigger": "time",
            "device": {"type": device_type, "risk": risk},
            "action": action,
            "mapping": {"status": status, "confidence": 0.9},
            "routine": None if independence is None else {"independence": independence, "accept_share": 0.8},
            "confidence": confidence,
        }
    )


@pytest.mark.fr("FR-06")
@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.9, Decision.ACT), (0.8, Decision.ACT), (0.6, Decision.ASK), (0.4, Decision.SUGGEST)],
)
def test_low_risk_follows_confidence(confidence, expected):
    assert decide(req(confidence=confidence), S).decision == expected


@pytest.mark.fr("FR-06")
def test_high_risk_on_always_asks_first():
    assert decide(req("heater", "high", "on", 0.99), S).decision == Decision.ASK
    assert decide(req("heater", "high", "on", 0.3), S).decision == Decision.SUGGEST


@pytest.mark.fr("FR-06")
def test_high_risk_off_follows_the_low_risk_rules():
    assert decide(req("heater", "high", "off", 0.9), S).decision == Decision.ACT
    assert decide(req("heater", "high", "off", 0.6), S).decision == Decision.ASK


@pytest.mark.fr("FR-06")
def test_the_enclave_sorting_wins_over_a_low_risk_from_the_hub():
    assert decide(req("heater", "low", "on", 0.99), S).decision == Decision.ASK


@pytest.mark.fr("FR-04")
def test_the_routine_independence_caps_the_decision():
    assert decide(req(independence="ask_first"), S).decision == Decision.ASK
    assert decide(req(independence="suggest_only"), S).decision == Decision.SUGGEST
    assert decide(req(confidence=0.6, independence="act_and_tell"), S).decision == Decision.ASK


@pytest.mark.fr("FR-06")
def test_no_act_without_a_verified_mapping():
    r = decide(req(status="Pending"), S)
    assert r.decision == Decision.SUGGEST
    assert r.reason == "the mapping is not Verified"
