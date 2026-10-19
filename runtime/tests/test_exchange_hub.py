#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.runtime.boundary.obs.exchange_hub import ExchangeHub, HubExchangePlugin, record_to_dict
from mas.runtime.driver.driver import ExchangeRecord


def test_record_to_dict_dataclass() -> None:
    rec = ExchangeRecord(kind="user_in", text="hi", agent_id="a")
    data = record_to_dict(rec)
    assert data["kind"] == "user_in"
    assert data["text"] == "hi"


def test_hub_buffer_and_publish() -> None:
    hub = ExchangeHub()
    hub.publish("s1", {"kind": "user_in", "text": "hi"})
    buffered = hub.buffer("s1")
    assert buffered[0]["replay"] is True
    assert buffered[0]["record"]["kind"] == "user_in"


def test_hub_plugin_publishes() -> None:
    hub = ExchangeHub()
    plugin = HubExchangePlugin(hub, "sid")
    plugin.on_exchange(ExchangeRecord(kind="tool_call", tool_name="search"))
    assert hub.buffer("sid")[0]["record"]["tool_name"] == "search"
