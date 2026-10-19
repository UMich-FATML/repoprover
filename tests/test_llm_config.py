# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""LLM configuration: comet gateway resolution, request extras, CLI flags."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from repoprover.agents.base import (
    COMET_DEFAULT_MAX_TOKENS,
    COMET_GATEWAY_FILE_ENV,
    AgentConfig,
    create_client,
    resolve_comet_base_url,
)
from repoprover.agents.tools import run_tool_loop
from repoprover.cli import agent_config_from_args


@pytest.fixture
def gateway_file(tmp_path, monkeypatch):
    path = tmp_path / "comet-gateway.json"
    path.write_text(json.dumps({"gateway_url": "http://gw-host:8000/", "probe_url": "http://127.0.0.1:8000"}))
    monkeypatch.setenv(COMET_GATEWAY_FILE_ENV, str(path))
    return path


def test_comet_base_url_from_gateway_file(gateway_file):
    assert resolve_comet_base_url() == "http://gw-host:8000/v1"


def test_comet_missing_gateway_file_names_fix(tmp_path, monkeypatch):
    monkeypatch.setenv(COMET_GATEWAY_FILE_ENV, str(tmp_path / "nope.json"))
    with pytest.raises(ValueError, match="--base-url"):
        resolve_comet_base_url()


def test_comet_gateway_file_without_url(tmp_path, monkeypatch):
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"probe_url": "http://127.0.0.1:8000"}))
    monkeypatch.setenv(COMET_GATEWAY_FILE_ENV, str(path))
    with pytest.raises(ValueError, match="gateway_url"):
        resolve_comet_base_url()


def test_default_config_is_kimi_on_comet():
    config = AgentConfig()
    assert config.provider == "comet"
    assert config.model == "kimi-k3"
    assert config.reasoning_effort == "max"
    assert (config.top_p, config.temperature) == (1.0, None)
    assert config.max_tokens == COMET_DEFAULT_MAX_TOKENS
    assert config.preserve_thinking is True
    assert config.extra_body() == {
        "reasoning_effort": "max",
        "chat_template_kwargs": {"preserve_thinking": True},
    }


def test_empty_provider_falls_back_to_comet():
    assert AgentConfig(provider="").provider == "comet"


def test_other_providers_keep_old_defaults():
    config = AgentConfig(provider="openai")
    assert config.model == "gpt-4o"
    assert config.max_tokens == 8192
    assert (config.temperature, config.top_p) == (0.7, None)
    assert config.preserve_thinking is False
    assert config.extra_body() is None


def test_comet_client_needs_no_key(gateway_file, monkeypatch):
    monkeypatch.delenv("COMET_API_KEY", raising=False)
    client = create_client(AgentConfig())
    assert str(client.base_url) == "http://gw-host:8000/v1/"
    assert "anthropic-beta" not in client.default_headers


def test_explicit_base_url_overrides_gateway_file(tmp_path, monkeypatch):
    monkeypatch.setenv(COMET_GATEWAY_FILE_ENV, str(tmp_path / "missing.json"))
    client = create_client(AgentConfig(base_url="http://other:9000/v1"))
    assert str(client.base_url) == "http://other:9000/v1/"


class _FakeCompletions:
    """Returns one tool call (with reasoning), then a final answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            tool_call = SimpleNamespace(id="add:0", function=SimpleNamespace(name="add", arguments='{"a": 2, "b": 3}'))
            message = SimpleNamespace(content="", reasoning_content="need add", tool_calls=[tool_call])
            finish = "tool_calls"
        else:
            message = SimpleNamespace(content="5", reasoning_content=None, tool_calls=None)
            finish = "stop"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


def _run(**kwargs: Any) -> tuple[_FakeCompletions, Any]:
    completions = _FakeCompletions()
    client: Any = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = run_tool_loop(
        client=client,
        model="kimi-k3",
        system_prompt="sys",
        initial_messages=[{"role": "user", "content": "add 2 and 3"}],
        tools=[{"type": "function", "function": {"name": "add", "parameters": {"type": "object"}}}],
        tool_handler=lambda name, args: str(args["a"] + args["b"]),
        **kwargs,
    )
    return completions, result


def test_tool_loop_forwards_extra_body_and_keeps_reasoning():
    extra = AgentConfig().extra_body()
    completions, result = _run(extra_body=extra, keep_reasoning=True)
    assert result.final_text == "5"
    assert all(call["extra_body"] == extra for call in completions.calls)
    assistant = completions.calls[1]["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "need add"


def test_tool_loop_defaults_send_no_extras():
    completions, _ = _run()
    assert all("extra_body" not in call for call in completions.calls)
    assert "reasoning_content" not in completions.calls[1]["messages"][2]


def test_tool_loop_recording_saves_system_prompt_reasoning_and_ids(tmp_path):
    """The recorder captures the system prompt, reasoning, and tool call ids."""
    from repoprover.recording import AgentRecorder

    recorder = AgentRecorder(tmp_path, "prove-001", "prove")
    completions, result = _run(recorder=recorder)
    assert result.final_text == "5"
    assert completions.calls

    events = [
        json.loads(line) for line in (tmp_path / "agents" / "prove-001.jsonl").read_text().splitlines() if line.strip()
    ]
    msgs = [e for e in events if e["event"] == "msg"]
    tools = [e for e in events if e["event"] == "tool"]

    # First msg is the system prompt, then the user message
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "sys"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "add 2 and 3"

    # Assistant message carries reasoning and the tool call id, even though
    # keep_reasoning defaults to False (recording is independent of history)
    assistant = msgs[2]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "need add"
    assert assistant["tool_calls"][0]["id"] == "add:0"
    assert assistant["tool_calls"][0]["name"] == "add"

    # Tool result event links back via tool_call_id
    assert tools[0]["name"] == "add"
    assert tools[0]["tool_call_id"] == "add:0"
    assert tools[0]["result"] == "5"


def _parse_run(*argv: str):
    import sys

    from repoprover import cli

    captured = {}

    def fake_cmd_run(args):
        captured["args"] = args
        return 0

    old_argv = sys.argv
    sys.argv = ["repoprover", "run", "/tmp/x", *argv]
    try:
        original = cli.cmd_run
        cli.cmd_run = fake_cmd_run
        cli.main()
    finally:
        cli.cmd_run = original
        sys.argv = old_argv
    return captured["args"]


def test_cli_defaults_build_comet_config():
    config = agent_config_from_args(_parse_run())
    assert (config.provider, config.model, config.reasoning_effort) == ("comet", "kimi-k3", "max")
    assert (config.max_tokens, config.top_p, config.temperature) == (65536, 1.0, None)


def test_cli_overrides():
    args = _parse_run(
        "--provider=openai",
        "--model=m",
        "--base-url=http://h/v1",
        "--max-tokens=100",
        "--temperature=0.1",
        "--skip-llm-check",
    )
    config = agent_config_from_args(args)
    assert (config.provider, config.model, config.base_url) == ("openai", "m", "http://h/v1")
    assert (config.max_tokens, config.temperature) == (100, 0.1)
    assert args.skip_llm_check is True


def test_tool_loop_sends_only_set_sampling_params():
    completions, _ = _run(temperature=None, top_p=1.0)
    assert all(call["top_p"] == 1.0 and "temperature" not in call for call in completions.calls)
