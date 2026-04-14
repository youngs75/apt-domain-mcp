# AGENTS.md

## Runtime
- Target runtime: PYTHON3.12
- Base language: Python 3.12
- Default package tooling: pip unless the repository already uses poetry, pip-tools, or another existing workflow
- This file is managed as the default AGENTS.md template for the PYTHON3.12 VS Code runtime workspace.

## Working Rules
- Keep changes focused on the requested feature or fix.
- Follow the existing repository structure, formatting, and dependency management instead of introducing a parallel workflow.
- Add or update a Dockerfile whenever you add or change a runnable app, MCP, agent, worker, or API service, even if Docker is not mentioned in the request.
- Add or update a runtime-appropriate .gitignore for this runtime workspace when you introduce or manage the repository.
- The .gitignore must cover generated files, virtual environments, caches, Python build artifacts, local databases, local env files, and other local-only artifacts commonly produced by the runtime.

## Dockerfile Rules
- Dockerfile and deployed container startup commands must use 0.0.0.0:8080.
- Treat port 8080 as the container-only deployment port for MCP and Agent workloads.
- For local development, use port 3000 by default unless the user explicitly requests another port or the existing project already standardizes on a different local port.
- Do not switch local development to 8080 unless there is an explicit reason to do so.
- If local and container ports differ, document the mapping in code comments, README notes, or startup scripts.

## LLM Rules
- When the implementation needs an LLM client, prefer LangChain with the LiteLLM provider.
- Read configuration from LITELLM_BASE_URL, LITELLM_API_KEY, and LITELLM_MODEL.
- Do not hardcode provider-specific URLs, API keys, or model names when those LiteLLM variables are available.
- Keep LiteLLM configuration injectable through environment variables so the same code works in local, workspace, MCP, and deployed environments.

## Delivery Checklist
- Verify the app starts in Python 3.12.
- Verify a runtime-appropriate .gitignore covers generated files and local-only config.
- Verify the Dockerfile exists and uses port 8080 for deployed execution.
- Verify local development uses port 3000 unless the repository already requires another port.
- Verify any LLM integration uses the LiteLLM environment variables.

