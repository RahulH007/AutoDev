from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from agents.pm_agent import pm_agent
from state.state import MultiAgent

def build_workflow():

    graph = StateGraph(MultiAgent)

    graph.add_node("PM_agent", pm_agent)

    graph.add_edge(START, "PM_agent")
    graph.add_edge("PM_agent", END)

    workflow = graph.compile()

    return workflow