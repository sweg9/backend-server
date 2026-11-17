"""C-02: every name in vocab.py is spelled exactly as in skills/standards/data-model.md."""

import re
from pathlib import Path

import pytest

from backend.common import vocab

DATA_MODEL = Path(__file__).resolve().parents[2] / "skills" / "standards" / "data-model.md"

ROWS = {
    "stage": vocab.Stage,
    "control `kind`": vocab.ControlKind,
    "control `state`": vocab.ControlState,
    "control `actions`": vocab.ControlAction,
    "mapping `status`": vocab.MappingStatus,
    "`channel`": vocab.Channel,
    "device `type`": vocab.DeviceType,
    "device `risk`": vocab.Risk,
    "`independence`": vocab.Independence,
    "routine `source`": vocab.RoutineSource,
    "routine feedback": vocab.RoutineFeedback,
    "`trigger`": vocab.Trigger,
    "check answer": vocab.CheckAnswer,
    "run `result`": vocab.RunResult,
    "hand option": vocab.HandOption,
}


def table() -> dict[str, list[str]]:
    out = {}
    for line in DATA_MODEL.read_text().splitlines():
        m = re.match(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
        if m and m.group(1) in ROWS:
            values = m.group(2).split(".")[0]  # the device type row adds a sentence after the list
            values = re.sub(r"\([^)]*\)", "", values)  # notes in brackets, such as the one on ARM
            out[m.group(1)] = re.findall(r"`([^`]+)`", values)
    return out


@pytest.mark.skipif(not DATA_MODEL.exists(), reason="skills submodule not checked out")
@pytest.mark.parametrize("row", sorted(ROWS))
def test_names_match_data_model(row):
    assert [e.value for e in ROWS[row]] == table()[row]
