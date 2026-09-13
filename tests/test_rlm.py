"""Offline tests. The kernel is a real subprocess; the models are faked."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from rlm.rlm import RLM, extract_code

STUB_SUBCALL = "def llm_call(prompt, model=None):\n    return 'SUB<' + prompt[:12] + '>'\n"


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


def _rlm(replies: list[str], **kw) -> tuple[RLM, MagicMock]:
    client = MagicMock()
    client.messages.create.side_effect = lambda **_: _Resp(replies.pop(0))
    return RLM(client=client, verbose=False, subcall_bootstrap=STUB_SUBCALL, max_turns=6, **kw), client


def test_final_variable_terminates():
    rlm, client = _rlm([
        "```python\nprint(len(context))\n```",
        "```python\nFinal = f'there are {context.count(\"fox\")} foxes'\n```",
    ])
    assert rlm.complete("count foxes", "fox fox fox dog") == "there are 3 foxes"
    assert client.messages.create.call_count == 2


def test_recursive_subcall_inside_kernel():
    rlm, _ = _rlm([
        "```python\nparts = [llm_call(context[i:i+5]) for i in range(0, 10, 5)]\nFinal = '|'.join(parts)\n```",
    ])
    assert rlm.complete("q", "abcdefghij") == "SUB<abcde>|SUB<fghij>"


def test_output_is_truncated_and_fed_back():
    seen = {}

    def create(**kw):
        seen["messages"] = kw["messages"]
        return _Resp(replies.pop(0))

    replies = ["```python\nprint('x' * 10000)\n```", "```python\nFinal = 'ok'\n```"]
    client = MagicMock()
    client.messages.create.side_effect = create
    rlm = RLM(client=client, verbose=False, subcall_bootstrap=STUB_SUBCALL, max_output_chars=100)
    assert rlm.complete("q", "ctx") == "ok"
    fed_back = seen["messages"][-2]["content"]
    assert "truncated" in fed_back and len(fed_back) < 300


def test_exceptions_surface_to_root_model():
    seen = {}

    def create(**kw):
        seen["messages"] = kw["messages"]
        return _Resp(replies.pop(0))

    replies = ["```python\n1/0\n```", "```python\nFinal = 'recovered'\n```"]
    client = MagicMock()
    client.messages.create.side_effect = create
    rlm = RLM(client=client, verbose=False, subcall_bootstrap=STUB_SUBCALL)
    assert rlm.complete("q", "ctx") == "recovered"
    assert "ZeroDivisionError" in seen["messages"][-2]["content"]


def test_no_code_block_is_nudged_not_fatal():
    rlm, _ = _rlm(["I need to think first.", "```python\nFinal = 'done'\n```"])
    assert rlm.complete("q", "ctx") == "done"


def test_gives_up_after_max_turns():
    rlm, _ = _rlm(["```python\nx = 1\n```"] * 6)
    with pytest.raises(RuntimeError):
        rlm.complete("q", "ctx")


def test_kernel_does_not_inherit_arbitrary_env(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "leak-me")
    rlm, _ = _rlm([
        "```python\nimport os\nFinal = os.environ.get('SUPER_SECRET_TOKEN', 'absent')\n```",
    ])
    assert rlm.complete("q", "ctx") == "absent"


def test_proxy_vars_do_reach_kernel(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://sandbox-proxy:3128")
    rlm, _ = _rlm(["```python\nimport os\nFinal = os.environ['HTTPS_PROXY']\n```"])
    assert rlm.complete("q", "ctx") == "http://sandbox-proxy:3128"


def test_extract_code_joins_multiple_blocks():
    reply = "first\n```python\na = 1\n```\nthen\n```py\nb = 2\n```"
    assert extract_code(reply) == "a = 1\n\nb = 2\n"
