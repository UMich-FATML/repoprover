# Plan: kimi-k3 on the comet gateway as the default LLM

Status: Complete (2026-10-19)

## Goal

`repoprover run` works out of the box with `kimi-k3` served by the comet
gateway. Before this change no CLI path set `AgentConfig`, so agents fell back
to `provider=""` and failed with "No base URL".

## Decision

RepoProver gives tools to the LLM itself (`agents/tools.py::run_tool_loop`), so
it calls kimi-k3 directly through the OpenAI SDK. No external harness (dsh).

## Changes

1. `agents/base.py`: a `comet` provider and the new default. The base URL comes
   from `gateway_url` in the gateway state file (`REPOPROVER_COMET_GATEWAY_FILE`
   overrides the path). No key is needed. Defaults are `reasoning_effort=high`,
   `preserve_thinking`, 32768 max tokens and a 1800 s timeout.
   `AgentConfig.extra_body()` holds the request extras.
2. `agents/tools.py`: `extra_body` is threaded through the loop, the retry and
   compaction. `keep_reasoning` keeps `reasoning_content` in the history.
3. `cli.py`: LLM flags on `run`, `agent_config_from_args`, and a startup
   endpoint/model check. The config reaches the coordinator, the workers and
   the mock workers.
4. `stool.py`: forwards the provider, model, base URL and reasoning effort.
5. Tests in `tests/test_llm_config.py`; docs in `project-setup.md`,
   `agents.md`, `design.md` and the README.

## Verification

`pixi run check` passes. A live `call_llm_simple` call, a tool-loop round trip
and the startup check all succeeded against the gateway.
