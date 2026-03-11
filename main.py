from graph.build_graph import build_workflow

workflow = build_workflow()

initial_state = {
    "user_requirements": "I want a simple Ecommerce website where users can browse products, add them to a cart"
}
result = workflow.invoke(initial_state)

print(result)