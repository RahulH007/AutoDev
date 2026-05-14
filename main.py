import sys
sys.stdout.reconfigure(encoding='utf-8')

from graph.build_graph import build_workflow

workflow = build_workflow()

initial_state = {
    "user_requirements": """ Build me a simple Expense tracker app with a web interface, where users can log in, add their expenses, and view reports. The app should have a backend API and a frontend interface.
""",
    "code_manifest": {},
    "qa_report": {},
    "retry_count": 0,
    "status": {}
}
config = {"configurable": {"thread_id": "1"}}

print("Starting workflow...")
for event in workflow.stream(initial_state, config):
    pass

result_state = None
while True:
    state = workflow.get_state(config)
    
    if not state.next:
        # Workflow finished
        print("\nWorkflow completed.")
        result_state = state.values
        break
    
    # Workflow interrupted
    next_step = state.next[0]
    print(f"\n--- Workflow paused. Next step to execute: {next_step} ---")
    user_input = input("Press Enter to approve and continue, or type 'exit' to stop: ")
    
    if user_input.strip().lower() == 'exit':
        print("\nExiting workflow.")
        result_state = state.values
        break
    
    # Resume the workflow
    print("\nResuming workflow...")
    for event in workflow.stream(None, config):
        pass

print("\n--- Final Status ---")
status_dict = result_state.get("status", {}) if result_state else {}
for k, v in status_dict.items():
    print(f"{k}: {v}")
