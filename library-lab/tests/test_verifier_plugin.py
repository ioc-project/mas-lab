#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from unittest.mock import MagicMock, patch

from mas.library.lab.plugins.design_patterns import verifier as verifier_mod
from mas.library.lab.plugins.design_patterns.verifier import DeterministicVerifierPlugin


def test_module_defines_one_verifier_plugin() -> None:
    plugins = [name for name, obj in vars(verifier_mod).items() if isinstance(obj, type) and name.endswith("Plugin")]
    assert plugins == ["DeterministicVerifierPlugin"]
    assert DeterministicVerifierPlugin.plugin_id == "deterministic_verifier@v1"


def test_verifier_asks_the_second_peer_to_review_the_proposal() -> None:
    plugin = DeterministicVerifierPlugin()
    st = plugin._state()
    st.participants = ["proposer", "checker"]
    st.round_num = 1
    st.next_idx = 1
    q = MagicMock()
    run = MagicMock()
    run.events = []
    config = MagicMock()
    with patch.object(verifier_mod, "dispatch_single", return_value=["ok"]) as mocked:
        out = plugin.evaluate_next(q, run, config=config)
    assert out == ["ok"]
    assert "Proposal:" in mocked.call_args.args[4]
    assert mocked.call_args.args[5] == "checker"
