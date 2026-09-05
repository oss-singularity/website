# Agent Atlas: editorial sources

The atlas is a curated starting point for exploring the agent ecosystem. It is
not a ranking, benchmark, endorsement, complete directory or affiliation claim.
Tools are selected for a useful, distinct role and identifiable official
sources. Open-source implementations, proprietary applications and commercial
hosted services are all eligible. They are presented without download counts,
star counts, price comparisons or assertions that one provider is best.

The initial 13 entries were checked against official project repositories and
documentation on **2026-09-04 UTC**. Twelve additions were checked on
**2026-09-05 UTC**, bringing the catalog to **25 entries**. Existing entries retain
their original review dates. The dataset's `updated` date records its latest
editorial change; each entry's `reviewed` date records a source review, not an
installation test, security audit or compatibility certification. Descriptions
are editorial paraphrases; use cases illustrate a practical fit. Entries are
alphabetical within their categories, without a preferred provider.

## Category boundaries

- **Coding:** applications for software work, including terminal tools, editors
  and hosted agents. Some, such as goose, also handle broader workflows.
- **Frameworks:** libraries developers use to build agents and workflows.
- **Local:** model execution infrastructure. A runtime is not a complete agent,
  and choosing a local-capable tool does not make every configuration offline.
- **Protocols:** interfaces for interoperability. MCP connects applications to
  tools and context; A2A connects independent agent applications; ACP connects
  coding agents and editors.

## Source, license and access

The `website` field is a useful official starting point. `source_url` identifies
an official reference: an implementation repository, a product repository or
the applicable product terms. A public repository can distribute releases and
documentation without publishing the application's implementation. It does not
by itself establish an open-source license.

The `license` field is a brief, scoped editorial label. For example, Codex's CLI
license does not describe its hosted service, and an SDK license does not supply
model access. Proprietary tools may have free or paid access paths; open-source
tools may use paid model services. Consult the upstream account, plan, provider
and deployment requirements. This catalog does not create accounts, execute
agents or establish an integration with the Commons API.

## Primary sources

| Entry | Official source | Scope verified |
| --- | --- | --- |
| Aider | [Repository and README](https://github.com/Aider-AI/aider) | Terminal pair programming, repository map, Git integration, cloud/local model support; Apache-2.0. |
| Cline | [Repository and README](https://github.com/cline/cline) | VS Code and CLI, edits and commands, approval controls, MCP; Apache-2.0 core. |
| goose | [Repository and README](https://github.com/aaif-goose/goose) | Desktop app, CLI, API, general workflows, model providers, MCP; Apache-2.0. |
| OpenCode | [Repository and README](https://github.com/anomalyco/opencode) | Terminal coding agent, build/plan modes and subagents; MIT. |
| OpenHands | [Current repository and README](https://github.com/OpenHands/OpenHands) | Agent Canvas, self-hosted coding-agent control center, local/container/remote backends and automations; MIT. |
| Qwen Code | [Repository and README](https://github.com/QwenLM/qwen-code) | Terminal and headless agent, provider APIs, local-model integrations, subagents and MCP; Apache-2.0. |
| CrewAI | [Repository and README](https://github.com/crewAIInc/crewAI) | Python framework, Crews, Flows and event-driven orchestration; MIT framework. |
| LangGraph | [Repository and README](https://github.com/langchain-ai/langgraph) | Stateful workflows, durable execution, memory and human oversight; MIT library. |
| smolagents | [Repository and README](https://github.com/huggingface/smolagents) | Python agent library, code actions, tools, model options and sandbox integrations; Apache-2.0. |
| Ollama | [Repository and README](https://github.com/ollama/ollama) | Model execution and management, CLI, local HTTP API, Python/JavaScript clients; MIT runtime. |
| llama.cpp | [Repository and README](https://github.com/ggml-org/llama.cpp) | C/C++ inference, quantization, CPU/GPU backends and server tooling; MIT engine. |
| Model Context Protocol | [Specification repository](https://github.com/modelcontextprotocol/modelcontextprotocol), [official introduction](https://modelcontextprotocol.io/introduction) | Open standard connecting AI applications with tools and context; repository identifies MIT license. |
| Agent2Agent | [Protocol repository and README](https://github.com/a2aproject/A2A) | Capability discovery and collaboration between independent agents; Apache-2.0 protocol repository. |

### Additions reviewed on 2026-09-05

| Entry | Official source | Scope verified |
| --- | --- | --- |
| OpenAI Codex | [CLI documentation](https://learn.chatgpt.com/docs/codex/cli), [cloud documentation](https://learn.chatgpt.com/docs/cloud), [component scope](https://learn.chatgpt.com/docs/open-source), [CLI license](https://github.com/openai/codex/blob/main/LICENSE) | Repository inspection, edits and commands through the CLI; configured cloud tasks with reviewable changes. Apache-2.0 applies to the CLI repository; IDE extension and cloud are not open source. |
| GitHub Copilot CLI | [Current documentation](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli), [product repository](https://github.com/github/copilot-cli), [CLI license](https://github.com/github/copilot-cli/blob/main/LICENSE.md) | Interactive and programmatic terminal work, GitHub context and tools. The custom proprietary CLI license is separate from Copilot service access; current docs also describe configurable model providers. |
| Claude Code | [Product documentation](https://code.claude.com/docs/en/overview), [repository license](https://github.com/anthropics/claude-code/blob/main/LICENSE.md) | Terminal, IDE, desktop and web surfaces; file edits, commands and MCP tools. Repository license reserves Anthropic's rights and refers to commercial terms. Supported access paths depend on the surface and provider. |
| Gemini CLI | [Repository and README](https://github.com/google-gemini/gemini-cli), [documentation](https://geminicli.com/docs/) | Terminal agent with file operations, shell commands, search grounding and MCP. Apache-2.0 CLI; account or API access and usage limits are separate. |
| Cursor | [Agent documentation](https://cursor.com/docs/agent/overview), [service terms](https://cursor.com/terms-of-service) | Editor agent with codebase search, file edits, terminal tools and reviewable changes. Proprietary product and service, with account and plan conditions; a source-code license is not implied. |
| Devin | [Product documentation](https://docs.devin.ai/get-started/devin-intro), [platform terms](https://cognition.com/legal/platform-terms-of-service) | Hosted repository work with a development workspace, code execution and draft changes for review. Proprietary service with account access. The listing does not repeat the vendor's performance or autonomy claims. |
| OpenAI Agents SDK | [Official SDK guide](https://developers.openai.com/api/docs/guides/agents), [Python repository](https://github.com/openai/openai-agents-python), [Python license](https://github.com/openai/openai-agents-python/blob/main/LICENSE) | Tools, handoffs, guardrails, state and tracing; Python and TypeScript implementations. MIT label refers to the linked Python SDK. Model service access is configured separately. |
| Google Agent Development Kit | [Documentation](https://adk.dev/), [Python repository](https://github.com/google/adk-python), [license](https://github.com/google/adk-python/blob/main/LICENSE) | Agent creation, evaluation, workflow orchestration and deployment. The Python implementation is Apache-2.0; model choice and hosting are separate decisions. |
| Microsoft Agent Framework | [Overview](https://learn.microsoft.com/en-us/agent-framework/overview/), [repository](https://github.com/microsoft/agent-framework), [license](https://github.com/microsoft/agent-framework/blob/main/LICENSE) | Python and .NET agent workflows, provider integrations, state and observability. MIT framework; related hosting and model services are not covered by that license label. |
| Pydantic AI | [Current documentation](https://pydantic.dev/docs/ai/overview/), [repository](https://github.com/pydantic/pydantic-ai), [license](https://github.com/pydantic/pydantic-ai/blob/main/LICENSE) | Python SDK with typed outputs, validated tool arguments and multiple model providers. MIT SDK; related hosted products and model services remain separate. |
| LM Studio | [Application documentation](https://lmstudio.ai/docs/app), [desktop app terms](https://lmstudio.ai/app-terms) | Local model management and execution, chat, MCP tools and HTTP APIs. The desktop app uses proprietary terms; bundled open-source engines and downloaded model weights have their own licenses. |
| Agent Client Protocol | [Introduction](https://agentclientprotocol.com/get-started/introduction), [protocol repository](https://github.com/agentclientprotocol/agent-client-protocol), [license](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/LICENSE) | Editor-to-coding-agent communication, protocol schemas and SDKs. Apache-2.0 protocol repository; distinct from A2A and MCP. |

## Editorial decisions and qualifications

- The current goose repository is `aaif-goose/goose`; the old `block/goose` URL
  redirects there. Its current README points to `goose-docs.ai` for documentation.
- Freshly opening `OpenHands/OpenHands` returned the Agent Canvas README. Older
  search excerpts described a previous repository layout with an `enterprise/`
  license exception. The atlas uses the current Agent Canvas scope and does not
  imply that every OpenHands commercial offering is MIT-licensed.
- The Cline README explicitly says its JetBrains plugins are not open sourced.
  This entry therefore names VS Code and the CLI, and labels the license as core.
- CrewAI's commercial AMP suite and LangGraph's related hosted services are
  distinct from the open libraries described here.
- Runtime and tool licenses do not establish the license of separately supplied
  model weights, hosted services, plugins, extensions or user data.
- Copilot CLI's current documentation describes access through Copilot plans
  and optional model-provider configuration. Its repository README contains
  older model examples. The catalog uses current documentation for capabilities
  and the repository's actual license for distribution scope.
- Codex's official documentation currently redirects several former
  `developers.openai.com/codex/` pages to `learn.chatgpt.com/docs/`. Links above
  use the destinations verified during this review. The Codex SDK and OpenAI
  Agents SDK are separate components; the framework entry describes the latter.
- The Google ADK documentation redirects to `adk.dev`; Pydantic AI's current
  README points to `pydantic.dev/docs/ai/`. The entries use these current official
  documentation locations.
- Qwen Code is included for its practical terminal and interoperability features,
  using the official Qwen project as the source. Country of origin is not a
  ranking criterion, inclusion restriction or claim about a project's quality.
- [Continue's official README](https://github.com/continuedev/continue) currently
  states that the repository is no longer actively maintained and is read-only,
  with a final 2.0.0 release. It is omitted from this initial starting directory
  rather than presented as an actively maintained recommendation.
- No telemetry, authentication, model-cost or sandbox defaults were tested during
  this source review. Visit the upstream documentation before configuring tools.

## Updating an entry

Use the project's current official repository and documentation. Check its
identity, redirect destination, maintenance notices, feature scope and license
before updating its review date. Avoid carrying forward a search excerpt when
the current primary page has changed. Keep descriptions useful and modest;
publish measured comparisons only with a reproducible method and evidence.
