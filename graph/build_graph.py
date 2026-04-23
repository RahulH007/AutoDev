from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from agents.architecture_agent import architecture_agent
from agents.pm_agent import pm_agent
from state.state import MultiAgent

def build_workflow():

    graph = StateGraph(MultiAgent)

    graph.add_node("PM_agent", pm_agent)
    graph.add_node("architecture_agent", architecture_agent)

    graph.add_edge(START, "PM_agent")
    graph.add_edge("PM_agent", "architecture_agent")

    workflow = graph.compile()

    return workflow