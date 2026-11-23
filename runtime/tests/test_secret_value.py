#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SecretValue dump paths never show the bytes."""

from __future__ import annotations

import copy
import dataclasses
import logging
import pickle

import pytest
import yaml
from mas.runtime.secrets.errors import SecretDenied
from mas.runtime.secrets.value import SecretValue
from secret_fakes import CANARY

LABEL = "<secret:openai>"


def test_text_forms_hide_the_value(caplog: pytest.LogCaptureFixture) -> None:
    held = SecretValue(CANARY, name="openai")
    assert str(held) == repr(held) == f"{held}" == "%s" % held == format(held, ">3") == LABEL
    assert LABEL in yaml.safe_dump({"k": held}) and CANARY not in yaml.safe_dump({"k": held})
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("mas.runtime.secrets").error("token %s %r", held, RuntimeError(held))
    assert CANARY not in caplog.text


def test_pickle_copy_and_asdict_hide_the_value() -> None:
    held = SecretValue(CANARY, name="openai")
    assert CANARY.encode() not in pickle.dumps(held)
    assert pickle.loads(pickle.dumps(held)) == LABEL
    assert copy.deepcopy([held])[0] is held

    @dataclasses.dataclass
    class Row:
        token: SecretValue

    assert CANARY not in repr(dataclasses.asdict(Row(held)))
    with pytest.raises(TypeError) as caught:
        held.__getstate__()
    assert CANARY not in str(caught.value)


def test_len_bool_no_iter_and_reveal() -> None:
    held = SecretValue(CANARY, name="openai")
    assert len(held) == len(CANARY) and held and not SecretValue("", name="empty")
    with pytest.raises(TypeError):
        iter(held)
    assert held.reveal() == CANARY
    raw = CANARY.encode("utf-8") + b"\xff\xfe"
    with pytest.raises(SecretDenied, match="not utf-8") as caught:
        SecretValue(raw, name="openai").reveal()
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert CANARY not in str(caught.value) and not any(isinstance(arg, bytes) for arg in caught.value.args)
    assert SecretValue(raw, name="openai").reveal_bytes() == raw
