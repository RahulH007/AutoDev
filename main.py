from graph.build_graph import build_workflow

workflow = build_workflow()

initial_state = {
    "user_requirements": """ I want to build an ai agent to search trending reddit post //
        and write a post.
"""}
result = workflow.invoke(initial_state)

print(result)