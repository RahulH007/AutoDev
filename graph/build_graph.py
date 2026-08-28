"""The pipeline graph.

::

    START -> pm_agent -> architecture_agent -> developer_agent -> static_gate
                                                    ^                 |
                                                    |            (parses?)
                                                    |                 v
                                                    +---- test_runner <- qa_agent

The graph interrupts after ``pm_agent`` and ``architecture_agent`` so a human can
approve the plan or send it back with feedback.

Two things decide whether the developer agent runs again, and they are asked in
increasing order of cost. ``static_gate`` parses the code, which is free; if it
fails there is no point paying for a review, so the graph loops straight back.
Only code that parses reaches ``qa_agent``, and the tests QA writes are then
actually executed by ``test_runner`` before ``qa_router`` gives its verdict.

Every edge out of a node is conditional because any node can fail, and a failed
node ends the run instead of passing its missing output downstream.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.architecture_agent import architecture_agent
from agents.developer_agent import developer_agent
from agents.gates import static_gate_node, test_runner_node
from agents.pm_agent import pm_agent
from agents.qa_agent import qa_agent
from core.config import get_settings
from core.logging import get_logger
from state.state import AgentStatus, MultiAgent, Stage

logger = get_logger(__name__)

# Node names are the Stage values, so state["current_stage"] always names a real node.
PM = Stage.PM.value
ARCHITECTURE = Stage.ARCHITECTURE.value
DEVELOPER = Stage.DEVELOPER.value
STATIC_GATE = Stage.STATIC_GATE.value
QA = Stage.QA.value
TEST_RUNNER = Stage.TEST_RUNNER.value

INTERRUPT_AFTER = [PM, ARCHITECTURE]


# ── Routers ──────────────────────────────────────────────────────


def _failed(state: MultiAgent, stage: Stage) -> bool:
    return (state.get("status") or {}).get(stage.value) == AgentStatus.FAILED.value


def _halt(stage: Stage) -> str:
    """End the run rather than hand the next agent nothing to work from.

    A node that failed produced no artifact, and every downstream agent reads the
    one before it. Continuing burns tokens writing code against an empty
    architecture and buries the real error under whatever breaks next.
    """
    logger.error("%s failed; ending the run", stage.label)
    return "END"


def pm_review_router(state: MultiAgent) -> str:
    """Loop back to the PM agent if the human asked for revisions."""
    if _failed(state, Stage.PM):
        return _halt(Stage.PM)
    return PM if (state.get("pm_feedback") or "").strip() else ARCHITECTURE


def architecture_review_router(state: MultiAgent) -> str:
    if _failed(state, Stage.ARCHITECTURE):
        return _halt(Stage.ARCHITECTURE)
    return ARCHITECTURE if (state.get("architect_feedback") or "").strip() else DEVELOPER


def developer_router(state: MultiAgent) -> str:
    """Only run the gates if there is code to check."""
    if _failed(state, Stage.DEVELOPER):
        return _halt(Stage.DEVELOPER)
    return STATIC_GATE


def qa_review_router(state: MultiAgent) -> str:
    if _failed(state, Stage.QA):
        return _halt(Stage.QA)
    return TEST_RUNNER


def static_gate_router(state: MultiAgent) -> str:
    """Send code that does not parse straight back, skipping the QA model call."""
    if _failed(state, Stage.STATIC_GATE):
        return _halt(Stage.STATIC_GATE)

    report = state.get("static_report") or {}

    if not report.get("ran") or report.get("passed", True):
        return QA

    if _out_of_retries(state):
        logger.error("Static gate still failing after the retry budget; ending the run")
        return "END"

    logger.info("Static gate failed; returning to the developer without a QA pass")
    return DEVELOPER


def qa_router(state: MultiAgent) -> str:
    """Decide whether the code needs another developer pass.

    Real test failures outrank the reviewer's opinion, so they are checked first.
    """
    if _failed(state, Stage.TEST_RUNNER):
        return _halt(Stage.TEST_RUNNER)

    if _out_of_retries(state):
        logger.info("Retry budget exhausted; finishing with the current result")
        return "END"

    reason = _needs_rework(state)
    if reason:
        logger.info("Returning to the developer: %s", reason)
        return DEVELOPER

    return "END"


def _needs_rework(state: MultiAgent) -> str:
    """Return a human-readable reason to loop, or an empty string to finish."""
    settings = get_settings()

    verification = state.get("verification_report") or {}
    services = verification.get("services") or []

    if verification.get("ran") and not verification.get("passed"):
        failed = sum(s.get("failed", 0) + s.get("errors", 0) for s in services)
        return f"{failed} test(s) failing"

    blocked = [s for s in services if s.get("error")]
    if blocked:
        return f"{len(blocked)} service(s) could not run their tests"

    qa = state.get("qa_report") or {}
    critical = qa.get("critical_issues", 0)
    if critical > 0:
        return f"{critical} critical bug(s) reported"

    for report in qa.get("service_reports") or []:
        # Default to 0 so a missing score fails closed rather than slipping through.
        score = report.get("code_quality_score", 0)
        if score < settings.min_quality_score:
            return f"{report.get('service_name', '?')} scored {score}/10"

    return ""


def _out_of_retries(state: MultiAgent) -> bool:
    return state.get("retry_count", 0) >= get_settings().max_developer_retries


# ── Assembly ─────────────────────────────────────────────────────


def build_workflow(checkpointer: object | None = None):
    """Compile the graph.

    ``checkpointer`` is injectable so the service layer can supply a SQLite saver
    while tests and the CLI fall back to an in-memory one.
    """
    graph = StateGraph(MultiAgent)

    graph.add_node(PM, pm_agent)
    graph.add_node(ARCHITECTURE, architecture_agent)
    graph.add_node(DEVELOPER, developer_agent)
    graph.add_node(STATIC_GATE, static_gate_node)
    graph.add_node(QA, qa_agent)
    graph.add_node(TEST_RUNNER, test_runner_node)

    graph.add_edge(START, PM)

    graph.add_conditional_edges(
        PM,
        pm_review_router,
        {PM: PM, ARCHITECTURE: ARCHITECTURE, "END": END},
    )
    graph.add_conditional_edges(
        ARCHITECTURE,
        architecture_review_router,
        {ARCHITECTURE: ARCHITECTURE, DEVELOPER: DEVELOPER, "END": END},
    )

    graph.add_conditional_edges(
        DEVELOPER,
        developer_router,
        {STATIC_GATE: STATIC_GATE, "END": END},
    )

    graph.add_conditional_edges(
        STATIC_GATE,
        static_gate_router,
        {DEVELOPER: DEVELOPER, QA: QA, "END": END},
    )

    graph.add_conditional_edges(
        QA,
        qa_review_router,
        {TEST_RUNNER: TEST_RUNNER, "END": END},
    )

    graph.add_conditional_edges(
        TEST_RUNNER,
        qa_router,
        {DEVELOPER: DEVELOPER, "END": END},
    )

    return graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_after=INTERRUPT_AFTER,
    )
