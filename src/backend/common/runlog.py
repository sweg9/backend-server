"""The run entry of skills/standards/execution-log.md and its rules L-01 to L-09."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from backend.common.messages import Attempt, Checks, Msg, StageItem
from backend.common.vocab import Channel, Decision, Risk, RunResult, Trigger

MAX_ARM_ATTEMPTS = 2  # L-04, FR-11


class RunEntry(Msg):
    run_id: str
    trigger: Trigger
    started_at: datetime
    intent: str | None
    device_id: str
    risk: Risk
    confidence: Annotated[float, Field(ge=0, le=1)]
    decision: Decision
    channel: Channel | None
    result: RunResult
    reason: str | None
    checks: Checks
    stages: list[StageItem]
    attempts: list[Attempt]
    explanation: str

    @model_validator(mode="after")
    def _rules(self):
        if self.result != RunResult.SUCCESS and not self.reason:
            raise ValueError("L-01/execution-log: reason is required unless the result is success")
        arms = [a for a in self.attempts if a.channel == Channel.ARM]
        if len(arms) > MAX_ARM_ATTEMPTS:
            raise ValueError(f"L-04: at most {MAX_ARM_ATTEMPTS} ARM attempts")
        if any(a.gate != "pass" for a in arms):
            raise ValueError("L-04: every ARM attempt needs gate: pass")
        seen_arm = False
        for a in self.attempts:
            if a.channel == Channel.ARM:
                seen_arm = True
            elif seen_arm:
                raise ValueError("L-05: ARM comes only after every wireless attempt")
        for a in self.attempts:
            expected: Literal["platform", "camera"] = (
                "platform" if a.channel in (Channel.WIFI, Channel.BT) else "camera"
            )
            if a.source is not None and a.source != expected:
                raise ValueError(f"L-09: source of {a.channel} is {expected}")
        if self.attempts and self.channel != self.attempts[-1].channel:
            raise ValueError("channel is the channel of the last attempt")
        return self
