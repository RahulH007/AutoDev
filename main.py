from graph.build_graph import build_workflow

workflow = build_workflow()

initial_state = {
    "user_requirements": """ I want to build an ai agent system to search trending Linkedin Posts and analysis the //
engagement of the posts and generate a  similar post with high engagement.
"""}
result = workflow.invoke(initial_state)

print(result)