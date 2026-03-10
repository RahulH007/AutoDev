from graph.build_graph import build_workflow

workflow = build_workflow()

initial_state = {
    "user_requirements": "Build a food delivery app that allows customers to place orders, track deliveries, and provide feedback."
}

result = workflow.invoke(initial_state)

print(result)