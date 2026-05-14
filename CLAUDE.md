# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the workflow
python main.py

# Install dependencies
pip install -r requirements.txt
```

The workflow runs interactively — after the PM Agent and Architecture Agent complete, execution pauses and prompts for human approval before continuing. Press Enter to proceed or type `exit` to stop.

## Environment Setup

Requires a `.env` file in the project root with:

```
GOOGLE_API_KEY=your_gemini_api_key
```

PDF generation requires DejaVu fonts in `utils/fonts/`:
- `DejaVuSans.ttf`
- `DejaVuSans-Bold.ttf`

These can be downloaded from https://dejavu-fonts.github.io/

## Architecture

This is a **LangGraph multi-agent system** that autonomously generates a full software project from a plain-text user requirement. The pipeline is:

```
user_requirements → PM Agent → Architecture Agent → Developer Agent → QA Agent
                                                          ↑                |
                                                          └── (retry loop) ┘
```

The graph is defined in `graph/build_graph.py` and uses `MemorySaver` for checkpointing. It interrupts after `PM_agent` and `architecture_agent` for human review before developer work begins.

### Agents (`agents/`)

Each agent follows the same pattern:
1. Pull relevant fields from `MultiAgent` state
2. Call a structured LLM (Pydantic schema) for JSON output
3. Call the plain LLM for a human-readable PDF document
4. Save both to `memory/`
5. Return updated state fields

| Agent | Reads from state | Writes to state |
|---|---|---|
| `pm_agent` | `user_requirements` | `prd` |
| `architecture_agent` | `user_requirements`, `prd` | `architecture` |
| `developer_agent` | `user_requirements`, `prd`, `architecture`, `qa_report` | `code_manifest`, `retry_count`, `status` |
| `qa_agent` | `prd`, `architecture`, `code_manifest` | `qa_report`, `status` |

### QA Retry Loop

`qa_router` in `build_graph.py` routes QA output back to the developer agent if:
- `qa_report.critical_issues > 0`, or
- any `service_report.code_quality_score < 7`

Maximum 3 retries (`retry_count >= 3` forces END regardless).

### State (`state/state.py`)

`MultiAgent` is a `TypedDict` with these fields:
- `user_requirements: str` — raw input
- `prd: ManagerSchema` — product requirements document
- `architecture: ArchitectSchema` — system architecture
- `code_manifest: Dict[str, Any]` — maps service names to list of `{file_path, description}`
- `qa_report: Dict[str, Any]` — QA analysis results
- `retry_count: int` — tracks developer/QA retry cycles
- `status: Dict[str, str]` — per-agent status (`PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`)

### Schemas (`schema/`)

Pydantic models define the exact structure the LLM must return for each agent. Key relationships:
- `ManagerSchema` — full PRD (features, user flows, modules, APIs, data entities, NFRs)
- `ArchitectSchema` — system design (services, databases, Docker config, env vars, folder structure)
- `DeveloperSchema` — generated code (services → files with full source code)
- `QASchema` — review results (bugs per severity, test cases, per-service quality scores)

### Prompts (`prompts/`)

Each agent has two prompts:
- `*_json_prompt.py` — instructs the LLM to produce structured JSON (used with `get_structured_llm`)
- `*_pdf_prompt.py` — instructs the LLM to produce a human-readable markdown document (used with `llm_call`)

### LLM Client (`utils/llm_client.py`)

Uses `gemini-2.5-flash` via `langchain-google-genai`:
- `get_structured_llm(schema)` — returns `llm.with_structured_output(schema)` for JSON extraction
- `llm_call(prompt)` — plain text generation for PDF content

### Outputs

- `memory/` — JSON and PDF artifacts from each agent run (persisted between runs)
- `outputs/source_code/<service_name>/` — generated source code files written by `developer_agent`
- `outputs/tests/<service_name>/` — generated test files written by `qa_agent`
