# Plan: any LLM provider (OpenAI, DeepSeek, Gemini, local models)

Status: **OpenAI implemented and measured** (`copilot/openai_backend.py`, configs `gpt-5.5`,
`gpt-5.5-noverify`; see ISS-38..41 in `issues_log.md`). DeepSeek, Gemini and local models: plan below.
The OpenAI adapter uses the Responses API with strict structured outputs; the generic
OpenAI-compatible adapter for the rest follows this page. How to add providers without
touching the parts that make the co-pilot safe.

## 1. What changes and what does not

Everything that enforces the hard rules is provider-independent and stays as is:
pre-filter, PII masking, pattern guards, routing rules, templates, eval, business case, CLI and
web page. Only two calls go to a model, and each has a narrow contract:

| Call | Contract | Today |
|---|---|---|
| Draft + classification | `run(masked_ticket) -> (LLMOutput, Usage)` | `AnthropicBackend` in `copilot/llm.py` |
| Verifier | `verify(masked_ticket, out) -> (Verdict, Usage)` | `LLMVerifier` in `copilot/verifier.py` |

A new provider is a new implementation of these two contracts. Because the safety layer sits
outside the model, a weaker model can produce fewer usable drafts but cannot ship a hard-rule
violation that the guards and the router would catch. That is exactly what the eval measures.

## 2. Target design

```
copilot/providers/
  base.py            Backend / Verifier protocols, LLMRefusal, StructuredOutputError, Usage helpers
  anthropic.py       current AnthropicBackend + verifier (moved, unchanged)
  openai_compat.py   one client for every OpenAI-compatible endpoint
  registry.py        "provider:model" → backend instance, from config/providers.toml
config/providers.toml
```

**One adapter covers most providers.** OpenAI, DeepSeek, Gemini (OpenAI-compatible endpoint),
Azure OpenAI, OpenRouter, and local servers (Ollama, vLLM, llama.cpp server, LM Studio) all speak
the OpenAI Chat Completions protocol. They differ in base URL, key, how strictly they enforce a
JSON schema, how they report cached tokens, and data terms: that goes into configuration, not code.

**Models are addressed as `provider:model`**, e.g. `anthropic:claude-sonnet-5-5`,
`openai:<model>`, `deepseek:<model>`, `gemini:<model>`, `ollama:<model>`, `vllm:<model>`. Configs
in `llm.py` keep their shape (`model`, `escalate_model`, `verify`, plus a new `verifier_model`), so
`eval --config X`, `compare` and `business-case` work for any provider.

`config/providers.toml` (sketch):

```toml
[openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
structured = "json_schema_strict"     # json_schema_strict | json_object | grammar
cached_tokens_field = "usage.prompt_tokens_details.cached_tokens"
pii_cleared = true                    # compliance sign-off for (masked) ticket text
[openai.prices]                       # USD per 1M tokens; filled from the provider's price page
"<model>" = { in = 0.0, out = 0.0, cache_read = 0.0 }

[deepseek]
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
structured = "json_object"
pii_cleared = false                   # data terms and processing location need legal review

[gemini]
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
api_key_env = "GEMINI_API_KEY"
structured = "json_schema_strict"

[ollama]
base_url = "http://localhost:11434/v1"
api_key_env = ""                      # local, no key
structured = "json_schema_strict"     # schema-constrained decoding on the server
pii_cleared = true                    # data never leaves the machine

[vllm]
base_url = "http://gpu-host:8000/v1"
structured = "json_schema_strict"     # guided decoding
pii_cleared = true
```

Prices, field names and structured-output support are checked against each provider's current
documentation when the adapter is written; the values above are placeholders.

## 3. The hard parts, and how each is handled

| Difference | Handling |
|---|---|
| **Structured output.** Claude parses into the Pydantic schema. Others range from strict JSON-schema enforcement to plain "JSON mode" to nothing. | Ladder: (1) strict JSON schema where supported, with the schema generated from `LLMOutput` and converted to the strict dialect (all fields required, `additionalProperties: false`, no defaults); (2) JSON mode + the schema in the prompt + Pydantic validation + one repair retry with the validation error; (3) still invalid → `StructuredOutputError` → the existing fail-safe (ticket to `agent_answer`). The eval reports the structured-output failure rate per provider. |
| **Prompt caching.** Claude caches explicitly (`cache_control`, 1 h). OpenAI, DeepSeek and Gemini cache prompt prefixes automatically; local servers reuse the KV cache. | Keep the system prompt byte-stable (it already is), so automatic prefix caching works everywhere. `Usage` reads cached tokens from the provider's field (`cached_tokens_field`) so cost and `cache_hit_share` stay correct. |
| **Reasoning / effort.** Claude `effort`; other APIs have their own reasoning settings or none. | `effort` is mapped per provider in the adapter, or ignored where there is no equivalent. |
| **Refusals and errors.** The server-side fallback is Claude-only. | A provider-level fallback chain in config (`fallback = ["openai:...", "ollama:..."]`) on errors and timeouts, before the fail-safe. **Rule: fallback only to providers with the same or stronger data clearance** (`pii_cleared`), so an outage never sends tickets somewhere compliance has not approved. |
| **Prompt tuned on Claude.** | One shared prompt first. Per-provider prompt variants only if the eval shows a specific failure, and then as data in `prompt.py`, still behind the same guards. |
| **Languages.** Smaller and local models are weaker in Vietnamese, Arabic, Indonesian. | Measured by the existing per-language metrics; the router can send languages a model handles poorly to a stronger config. |

## 4. Verifier: independent by design

The verifier is a separate contract, so the drafter and the verifier can come from different
providers (e.g. a local model drafts, Claude Haiku verifies, or the reverse). Two different models
are less likely to make the same mistake than one model checking itself. Config gets
`verifier_model = "provider:model"`; the eval measures verifier rejections per pair.

## 5. Local models

| Runtime | When | Notes |
|---|---|---|
| Ollama | development, single machine | easiest setup; OpenAI-compatible endpoint; schema-constrained output |
| llama.cpp server | CPU only | slow but works; quantized 7-14B models |
| vLLM | production on a GPU | OpenAI-compatible, guided JSON decoding, prefix caching, batching |

Why local: ticket text never leaves the company (no DPA needed, PII masking becomes defence in
depth), cost is fixed hardware instead of per token. Cost: lower draft quality (more abstains,
weaker non-English), GPU operations. Volume is small (~14 tickets/hour), so one GPU server is
enough; on this CPU-only build machine a 7-8B quantized model is expected to take tens of seconds
per ticket, which still fits ~14 tickets/hour but must be measured.

## 6. Why not LiteLLM / LangChain

A thin own adapter over the OpenAI protocol covers every provider above with ~150 lines and keeps
full control over the three things this project depends on: strict schemas, cached-token
accounting and the PII-aware fallback rule. LiteLLM is the alternative if the provider list grows
well beyond these; it would plug in behind the same two contracts.

## 7. Steps

| # | Step | Estimate | Done when |
|---|---|---|---|
| 1 | Move backends into `copilot/providers/`, registry, prices into `providers.toml`; no behaviour change | 0.5 day | all tests and the mock gate pass unchanged |
| 2 | `openai_compat.py`: strict-schema conversion, JSON-mode + repair path, usage and cache mapping | 1 day | contract tests against a fake HTTP server for each structured mode |
| 3 | Presets for OpenAI, DeepSeek, Gemini, Ollama, vLLM | 0.5 day | `smoke --config <provider>` passes where a key or local server exists |
| 4 | Provider-independent verifier, `verifier_model` in configs | 0.5 day | cross-provider config runs end to end |
| 5 | Fallback chain with the `pii_cleared` rule | 0.5 day | test: an uncleared provider is never used as a fallback |
| 6 | Eval every provider on the same 92 tickets; `compare`; regenerate the business case | 0.5 day + API cost | table with safety bar, usable drafts, structured-output failures, $/ticket, latency |
| 7 | Docs: decisions, phase 2, issues log | 0.5 day | |

About 4 days. Acceptance for any provider: passes the same safety gate (0 hard-rule violations,
100% escalation recall, no errors), structured-output failures below 1%, and only masked text in
every request (asserted in the contract tests, as `tests/test_pii.py` does today).

## 8. Compliance note

Masking reduces what leaves the company but does not replace a provider review. For a regulated
broker each external provider needs a data processing agreement, retention terms and a processing
location the regulator accepts. Hosted DeepSeek in particular needs a legal review first; its open
weights can instead run locally under `vllm` or `ollama`, which removes the question.
