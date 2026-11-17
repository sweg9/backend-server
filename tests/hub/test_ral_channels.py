"""C-13 interfaces: the script RAL and the stub channel adapter."""

import asyncio

import pytest

from backend.common.vocab import Channel, DeviceAction, HandOption
from backend.hub.channels.stub import StubAdapter
from backend.hub.ral import make_ral


@pytest.mark.fr("FR-10")
def test_no_press_after_stop():
    ral = make_ral(HandOption.SCRIPT)

    async def go():
        await ral.stop()
        with pytest.raises(RuntimeError):
            await ral.press("A1", 400)

    asyncio.run(go())


def test_arm_is_not_a_wireless_adapter():
    with pytest.raises(ValueError):
        StubAdapter(Channel.ARM)


@pytest.mark.fr("FR-07")
def test_stub_records_what_was_sent():
    a = StubAdapter(Channel.IR)
    asyncio.run(a.send("dev_lamp_01", None, DeviceAction.ON))
    assert a.sent == [("dev_lamp_01", DeviceAction.ON)]
