# Agent Atlas: editorial sources

The atlas is a curated starting point for exploring the agent ecosystem. It is
not a ranking, benchmark, endorsement, complete directory or affiliation claim.
Projects are selected for a useful, distinct role and an identifiable official
source. They are presented without download counts, star counts, pricing claims
or assertions that one provider is best.

The initial 13 entries were checked against official project repositories and
documentation on **2026-09-05**. The `reviewed` date records a source review, not
an installation test, security audit or compatibility certification. Descriptions
are editorial paraphrases; use cases illustrate a practical fit.

## Category boundaries

- **Coding:** tools people can use for software work. Some, such as goose, also
  handle broader workflows.
- **Frameworks:** libraries developers use to build agents and workflows.
- **Local:** model execution infrastructure. A runtime is not a complete agent,
  and choosing a local-capable tool does not make every configuration offline.
- **Protocols:** interfaces for interoperability. MCP connects applications to
  tools and context; A2A connects independent agent applications.

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
