<div align="center">
  <h1>AgentForge</h1>
  <p><em>A LangGraph multi-agent system that turns a plain-text requirement into a verified, runnable codebase.</em></p>
</div>

---

## Overview

You describe an application in one paragraph. Four specialized agents then execute in sequence — Product
Manager, Architect, Developer, and QA — each communicating through a strict Pydantic contract rather than
free-form text. Every stage emits both a machine-readable JSON document for the next agent and a
human-readable PDF for the client. The generated code is then actually installed and executed, and real
failures are fed back to the developer agent until it passes.

## Pipeline

```text
START
  -> pm_agent            requirement          -> PRD
       (human review gate: approve or request revisions)
  -> architecture_agent  PRD                  -> system architecture
       (human review gate: approve or request revisions)
  -> developer_agent     architecture         -> source code on disk
  -> static_gate         source code          -> compile and lint results
  -> qa_agent            source code          -> bug report, quality scores, test files
  -> test_runner         tests                -> real pytest results
  -> END (or back to developer_agent, up to 3 attempts)
```

The graph is defined in `graph/build_graph.py`. It interrupts after `pm_agent` and `architecture_agent` so a
human can approve or send revision feedback, which loops the agent back over its own previous output.

The retry loop is driven by evidence, not vibes: `static_gate` short-circuits straight back to the developer
on compile errors without paying for an LLM review, and `test_runner` executes the tests QA wrote so the fix
prompt contains genuine tracebacks.

## Quickstart

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
cp .env.example .env           # then add your GOOGLE_API_KEY

python scripts/check_providers.py   # confirm your key works
python main.py "Build an expense tracker with login, expense CRUD, and monthly reports"
```

The run pauses twice for review. Press Enter to approve, type feedback to request a revision, or type
`exit` to abort.

Other CLI forms:

```bash
python main.py                              # prompts for the requirement, or reads stdin when piped
python main.py --file requirement.txt       # read it from a file
python main.py "..." --name expense-tracker # name the run
python main.py "..." --yes                  # skip both review gates
python main.py --list                       # list previous runs
```

Everything a run produces lands in `runs/<run_id>/`: `docs/` for the JSON and PDF artifacts, `source/` and
`tests/` for the generated project, `artifacts/` for the final zip, and `run.log` for the full transcript.

## Repository layout

```text
main.py                  CLI entry point
core/                    settings, structured logging, run workspaces and path safety
llm/                     provider registry, retry and fallback
agents/                  the four agents
prompts/                 two prompts per agent (structured JSON + client-facing PDF)
schema/                  Pydantic contracts between agents
verification/            execution runner, static gates, pytest runner
graph/                   LangGraph wiring and routers
state/                   the shared MultiAgent state
server/                  FastAPI service, SQLite persistence, run orchestration
frontend/                Next.js console
scripts/                 operational helpers
tests/                   test suite for this system (not the generated code)
runs/<run_id>/           per-run artifacts, generated source, generated tests
```

## Agents

| Agent | Reads | Writes | Contract |
|---|---|---|---|
| `pm_agent` | `user_requirements`, `pm_feedback` | `prd` | `ManagerSchema` |
| `architecture_agent` | `prd`, `architect_feedback` | `architecture` | `ArchitectSchema` |
| `developer_agent` | `prd`, `architecture`, failure reports | `code_manifest` | `DeveloperSchema` |
| `qa_agent` | `code_manifest`, source on disk | `qa_report` | `QASchema` |

QA runs a two-step review to control cost: a cheap triage call reads only the file manifest and returns the
paths worth inspecting, then only those files are read from disk and sent for scoring.

## Configuration

All settings live in `core/config.py` and can be set via `.env` or environment variables. The essentials:

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | — | Gemini credentials (required for the default provider) |
| `LLM_PROVIDER` | `google` | `google`, `groq`, `openai`, `ollama`, or `cerebras` |
| `RUNNER_BACKEND` | `local` | `local` subprocess execution (`docker` is a stub) |
| `LLM_TOKENS_PER_MINUTE` | `12000` | Per-minute token ceiling the pipeline paces itself under |
| `LLM_MAX_RETRIES` | `3` | Attempts for a *transient* failure; other failures get one |
| `LLM_RETRY_BACKOFF_SECONDS` | `2.0` | First retry wait, doubling, unless the provider names its own |
| `LLM_RETRY_MAX_DELAY_SECONDS` | `60.0` | Cap on any single retry wait |
| `MAX_DEVELOPER_RETRIES` | `3` | Cap on developer attempts before the graph ends |
| `MIN_QUALITY_SCORE` | `7` | Lowest QA score per service that still passes |
| `RUNS_DIR` | `runs` | Where per-run workspaces are written |
| `CORS_ORIGINS` | `http://localhost:3000` | Origins allowed to call the API |

Models are chosen per *purpose* rather than per agent, so you can move spend to where it matters:
`MODEL_HEAVY` (code generation), `MODEL_STRUCTURED` (PRD, architecture, QA), `MODEL_TEXT` (the PDF prose),
and `MODEL_CHEAP` (QA file triage). See `.env.example` for the full list with defaults.

### Staying inside the rate limit

Providers meter tokens over a rolling minute, so a pipeline that simply calls as fast as it can will have a
run killed partway through by a `429`. `llm/budget.py` holds a rolling window per provider and model:
each call reserves an estimate before it is sent, waits if there is no room, and reconciles against the real
usage the provider reports afterwards.

The budget belongs to the account, not the agent. Groq meters per organisation, so every agent using one
model shares a single `TokenBudget` — four agents holding four private budgets would commit four times the
real quota. Extra API keys cut from the same account do not raise the ceiling for the same reason.

The window rolls rather than resetting on the minute. A fixed window lets a burst either side of the
boundary put twice the limit into the provider's own rolling window, which is the failure being avoided.

Waits are logged, so a run that goes quiet says why in the console. One call larger than the entire budget
raises `BudgetExceededError` rather than waiting forever — no window will ever fit it, and the work has to
be split or the ceiling raised.

`LLM_TOKENS_PER_MINUTE` is a starting guess, and a wrong one cannot be counted around. A real run paced
against the configured 12,000 while Groq enforced 8,000 for that model, and was rejected with its own
window still showing room to spare. The rejection says which number is true —

```text
on tokens per minute (TPM): Limit 8000, Used 5276, Requested 5857
```

— so the budget adopts both figures: the ceiling, and the usage the provider says it is holding. That
second number matters on its own, because a structured call hands back a schema object with the token
usage already stripped off, leaving the local estimate to stand unreconciled for the whole minute. The
learning only happens on rejection, so a ceiling set too *low* is never corrected upward.

### Retrying only what is worth retrying

Not every failed call is worth repeating. A `429` is: the same call in ten seconds may well succeed. A
`400` saying the model would not call a tool is not — the prompt, the model and the schema are unchanged,
so the second attempt fails exactly like the first. Repeating it spends the token budget the next agent
needs, which is how a rate limit and a capability failure turn into one dead run.

`llm/errors.py` reads the provider's status code — from the exception, its response, or the message text
when LangChain has re-raised it as a plain error — and sorts the failure into one of three outcomes:

| Disposition | Examples | What happens |
| --- | --- | --- |
| retry | `429`, `5xx`, timeouts, dropped connections | Same call again, after the wait the provider asked for, or exponential backoff if it did not say |
| degrade | `400` tool-call or JSON-schema failures, validation errors | Straight to the next strategy or provider, no repeat |
| abort | bad key, decommissioned model, a call larger than the whole budget | Stop; no weaker strategy on the same model can help |

An unrecognised error degrades rather than retries: falling through costs one attempt on the next rung,
while repeating an unknown deterministic failure costs the budget.

The strategy ladder reports the error it *ended* on rather than the first one it hit. The strongest
mechanism fails first and is the least likely to explain why a run stopped — that is how a run whose real
problem was a rate limit came to be filed under a tool-calling error.

### Swapping models

Agents ask for a schema, never for a mechanism. `llm/structured.py` gives each model a ladder of
extraction strategies and uses the strongest one that works:

| Rung | How the schema is enforced | Needs |
|---|---|---|
| `native` | provider default, normally tool calling | tool-calling support |
| `json_schema` | provider constrains decoding to the schema | constrained decoding |
| `json_mode` | provider guarantees valid JSON, schema goes in the prompt | a JSON mode |
| `parse` | plain text, then locate and validate the JSON | nothing but text |

Rungs a provider cannot build are skipped when the chain is constructed. Because the last rung asks for
nothing but text, any chat model can drive the pipeline — including a local Ollama model with no tool
support. This is not theoretical: `llama-3.1-8b-instant` returns the JSON as literal text wrapped in
`<function=Schema> {...}`, which Groq rejects as `tool_use_failed`. The ladder recovers it.

Only the preferred rung is retried, since the lower ones exist precisely because it already failed.

PDF generation needs DejaVu fonts in `utils/fonts/` (`DejaVuSans.ttf`, `DejaVuSans-Bold.ttf`), available from
[dejavu-fonts.github.io](https://dejavu-fonts.github.io/).

## Running the service and console

The CLI and the web console are two clients of the same `RunService` and the same SQLite database, so a run
started in one is visible in the other.

```bash
python scripts/dev.py                       # API on :8000, docs at /docs
cd frontend && npm install && npm run dev   # console on :3000
```

Use `scripts/dev.py` rather than `uvicorn --reload` directly. The bare reloader watches the whole tree, so the
code the developer agent writes into `runs/` looks like a source edit and restarts the server mid-run, failing
the run that produced it. The script watches only the source packages. For production, drop the reloader:
`uvicorn server.app:app --port 8000`.

The console points at `http://127.0.0.1:8000` by default. To change that, copy
`frontend/.env.local.example` to `frontend/.env.local` and set `NEXT_PUBLIC_API_URL`. If you serve the
console from somewhere other than `localhost:3000`, add that origin to `CORS_ORIGINS` in `.env`.

| Route | Purpose |
|---|---|
| `POST /api/runs` | Start a run |
| `GET /api/runs` | List runs |
| `GET /api/runs/{id}` | Run state, stage statuses, reports, artifacts |
| `POST /api/runs/{id}/approve` | Resume a run paused for review |
| `POST /api/runs/{id}/feedback` | Send revision feedback to the paused stage |
| `POST /api/runs/{id}/cancel` | Stop a run |
| `GET /api/runs/{id}/events` | SSE stream of live events, with a `?since=` replay cursor |
| `GET /api/runs/{id}/log` | Historical log lines |
| `GET /api/runs/{id}/files` | Generated file tree, and `/files/{path}` to read one |
| `GET /api/runs/{id}/zip` | Download the generated project |

## Development

```bash
pip install -r requirements-dev.txt
ruff check .
pytest

cd frontend && npx tsc --noEmit && npm run build
```

The test suite uses a fake LLM that returns canned schema objects, so the entire pipeline is exercised
offline with no API calls and no network. That is also the fastest way to see the whole system work end to
end without spending anything.

## A note on executing generated code

`test_runner` installs and runs code written by a language model. `LocalSubprocessRunner` constrains it with
a wall-clock timeout, an output size cap, a working directory confined to the run workspace, and a scrubbed
environment so your API keys are never visible to the generated process. That is a reasonable boundary for a
development machine, but it is not kernel-level isolation: the code still runs as your user. Don't point
this at a requirement you don't trust, and don't run it on a host you care about.

`RUNNER_BACKEND=docker` is where container isolation would plug in. The `Runner` protocol is defined and the
graph selects the backend from config, but `DockerRunner` is a stub — selecting it raises
`NotImplementedError` rather than silently falling back to local execution.

## Scope

This is set up for local development. There is no Dockerfile, no CI workflow, no auth on the API, and no
token-cost accounting; the API binds to localhost and assumes a single trusted user.
