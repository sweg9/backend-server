"""Act, ask or suggest from risk and confidence: the `decide` request (FR-06, C-03).

Runs in the Nitro Enclave with settings/risk.yaml. The most careful of these wins:
- risk and confidence (wiki: Introduction > Autonomy by risk);
- the routine's independence, which the resident can lower (FR-04);
- the mapping: without a Verified mapping the system does not act (C-08).
"""

from backend.common.messages import Decide, DecideResult
from backend.common.settings import RiskSettings
from backend.common.vocab import Decision, DeviceAction, Independence, MappingStatus, Risk

CAREFUL = [Decision.ACT, Decision.ASK, Decision.SUGGEST]  # from least to most careful
CAP = {
    Independence.ACT_AND_TELL: Decision.ACT,
    Independence.ASK_FIRST: Decision.ASK,
    Independence.SUGGEST_ONLY: Decision.SUGGEST,
}


def _careful(*decisions: Decision) -> Decision:
    return max(decisions, key=CAREFUL.index)


def by_risk(risk: Risk, action: DeviceAction, confidence: float, s: RiskSettings) -> Decision:
    if confidence < s.confidence_low:
        return Decision.SUGGEST
    if risk == Risk.HIGH and action == DeviceAction.ON:
        return Decision.ASK  # turning a high-risk device on always asks first
    return Decision.ACT if confidence >= s.confidence_high else Decision.ASK


def decide(req: Decide, s: RiskSettings) -> DecideResult:
    # The hub sends the risk it stored, but the enclave's own sorting is the one that counts (C-03).
    risk = Risk.HIGH if Risk.HIGH in (req.device.risk, s.risk.get(req.device.type, Risk.HIGH)) else Risk.LOW
    decision = by_risk(risk, req.action, req.confidence, s)
    reason = f"{risk} risk, confidence {req.confidence:.2f}"
    if req.routine is not None and CAREFUL.index(CAP[req.routine.independence]) > CAREFUL.index(decision):
        decision = CAP[req.routine.independence]
        reason = f"the routine is set to {req.routine.independence}"
    if req.mapping.status != MappingStatus.VERIFIED:
        decision = _careful(decision, Decision.SUGGEST)
        reason = "the mapping is not Verified"
    return DecideResult(decision=decision, confidence=req.confidence, reason=reason)
