from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from agents.architecture_agent import architecture_agent
from agents.pm_agent import pm_agent
from agents.developer_agent import developer_agent
from agents.qa_agent import qa_agent
from state.state import MultiAgent

def qa_router(state: MultiAgent):
    qa = state.get("qa_report", {})
    retries = state.get("retry_count", 0)
    
    if retries >= 3:
        return "END"
        
    if qa.get("critical_issues", 0) > 0:
        return "developer_agent"
        
    for report in qa.get("service_reports", []):
        if report.get("code_quality_score", 10) < 7:
            return "developer_agent"
            
    return "END"

from langgraph.checkpoint.memory import MemorySaver

def build_workflow():

    graph = StateGraph(MultiAgent)

    graph.add_node("PM_agent", pm_agent)
    graph.add_node("architecture_agent", architecture_agent)
    graph.add_node("developer_agent", developer_agent)
    graph.add_node("qa_agent", qa_agent)

    graph.add_edge(START, "PM_agent")
    graph.add_edge("PM_agent", "architecture_agent")
    graph.add_edge("architecture_agent", "developer_agent")
    graph.add_edge("developer_agent", "qa_agent")

    graph.add_conditional_edges(
        "qa_agent",
        qa_router,
        {
            "developer_agent": "developer_agent",
            "END": END
        }
    )

    memory = MemorySaver()
    workflow = graph.compile(
        checkpointer=memory,
        interrupt_after=["PM_agent", "architecture_agent"]
    )

    return workflow