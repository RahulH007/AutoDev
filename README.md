<div align="center">
  <h1>AutoDev</h1>
  <p><em>An End-to-End AI Software Factory: From a single prompt to tested, deployed code.</em></p>
  
  
</div>

---

## Overview

Welcome to the **Autonomous Multi-Agent Dev Team**, a revolutionary framework that completely automates the software development lifecycle (SDLC). By simply providing a natural language prompt describing an application idea, this system spins up a virtual team of highly specialized AI agents. 

These agents communicate using strict data contracts (Pydantic schemas) to analyze requirements, architect the system, write the code, test it for bugs, and finally package it for deployment. It is an end-to-end software factory residing directly on your machine.

---

## How It Works (The Pipeline)

Our virtual tech company consists of 6 core agents acting in a sequential and iterative pipeline:

### 1. Product Manager (PM) Agent
- **Input:** Your raw application idea (e.g., "Build a LinkedIn post generator with engagement analytics").
- **Action:** Analyzes user demographics, feature requirements, and project scope.
- **Output:** A comprehensive **Product Requirements Document (PRD)** (JSON & PDF).

### 2. Architecture Agent
- **Input:** The PRD.
- **Action:** Designs the technical blueprint. It selects the tech stack, designs database schemas, maps out the folder structure, and writes strict API contracts.
- **Output:** An **Architecture Design Document** (JSON & PDF).

### 3. Master Dev Agent
- **Input:** The Architecture Document.
- **Action:** Acts as the Lead Engineer. It breaks down the architecture into a manageable **Task Queue** and orchestrates the worker agents. It is responsible for merging the final codebase.
- **Output:** Task assignments and the final merged source code.

### 4. Slave Dev Agents
- **Input:** Specific, scoped tasks from the Master Dev.
- **Action:** Writes the actual source code for specific modules, frontend components, or backend endpoints.
- **Output:** Raw Python, JS, HTML, or CSS code snippets.

### 5. QA (Quality Assurance) Agent
- **Input:** The compiled source code from the Master Dev.
- **Action:** Writes and executes unit test suites (e.g., using `pytest`). If a test fails, it initiates a **Feedback Loop**, sending a bug report back to the Slave Devs to fix the code until it passes.
- **Output:** Test suites, execution logs, and a **Bug Report / QA Sign-off**.

### 6. DevOps Agent
- **Input:** The fully tested and QA-approved codebase.
- **Action:** Prepares the application for the real world. It generates a `Dockerfile`, sets up CI/CD pipelines (e.g., GitHub Actions), and writes a project-specific README.
- **Output:** Deployment artifacts ready for AWS, Vercel, or local Docker execution.

---

## The Complete Repository Blueprint

```text
major-project/
├── main.py                     # Entry point to trigger the entire SDLC pipeline
├── config.py                   # Global configs (LLM models, API keys, retries)
├── requirements.txt            # Project dependencies
├── .env                        # Environment variables
│
├── agents/                     # The AI workforce
│   ├── base_agent.py           # Shared LLM communication and logging logic
│   ├── pm_agent.py             
│   ├── architecture_agent.py   
│   ├── master_dev_agent.py     
│   ├── slave_dev_agent.py      
│   ├── qa_agent.py             
│   └── devops_agent.py         
│
├── schema/                     # Pydantic models for strict inter-agent communication
│   ├── product_manager_schema.py
│   ├── architect_schema.py
│   ├── task_schema.py
│   └── report_schema.py
│
├── prompts/                    # Carefully engineered system prompts for each role
│   ├── pm_json_prompt.py
│   ├── architect_json_prompt.py
│   └── ...
│
├── pipeline/                   # Pipeline orchestration and logic loops
│   ├── orchestrator.py         # The LangGraph / StateGraph workflow engine
│   └── fix_loop.py             # QA -> Dev feedback loop logic
│
├── utils/                      # Utilities
│   ├── llm_client.py           # Wrapper for Gemini / LangChain
│   ├── json_utils.py           # File I/O
│   ├── pdf_util.py             # PDF Document generation
│   └── logger.py               # Centralized structured logging
│
├── state/                      # Runtime state definition
│   └── state.py                # TypedDict representing the pipeline memory
│
└── outputs/                    # WHERE THE MAGIC HAPPENS (Generated Artifacts)
    ├── prd/                    # Generated PRDs (JSON/PDF)
    ├── architecture/           # System designs (JSON/PDF)
    ├── source_code/            # The final generated application code
    ├── tests/                  # Generated unit tests
    └── devops/                 # Dockerfiles and deployment scripts
```

---

## Getting Started

### 1. Clone & Install
```bash
git clone https://github.com/yourusername/major-project.git
cd major-project

python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment Setup
Create a `.env` file in the root directory:
```env
GOOGLE_API_KEY=your_gemini_api_key_here
# Optional: Model tuning
LLM_TEMPERATURE=0.2
```

### 3. Run the Factory
Open `main.py` and set your million-dollar idea in the `initial_state`:
```python
initial_state = {
    "user_requirements": "Build a real-time collaborative markdown editor web app using WebSockets and a Python backend."
}
```
Then, execute the pipeline:
```bash
python main.py
```

### 4. Observe the Results
Watch the terminal as the agents converse, plan, code, and test. Once the pipeline completes, navigate to the `outputs/` folder. You will find:
1. Your professional PRD (`outputs/prd/`).
2. Your system architecture (`outputs/architecture/`).
3. Your fully functional code (`outputs/source_code/`).
4. Your deployment-ready Docker setup (`outputs/devops/`).

---

## Reliability & Self-Correction

One of the standout features of this project is the **QA Feedback Loop**. 
Unlike standard code generators that output code and hope for the best, our system tests its own code. If the QA Agent encounters an error during `pytest` execution, it extracts the stack trace and feeds it back to the Slave Dev agent, demanding a patch. The pipeline does not proceed to the DevOps phase until tests pass.

---

<div align="center">
  <i>Welcome to the future of software development.</i>
</div>
