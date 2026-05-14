import json
from agents.developer_agent import developer_agent
from agents.qa_agent import qa_agent
from state.state import MultiAgent

def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

print("Loading mock state from memory...")
# Load previously generated JSONs to use as input state
try:
    prd_data = load_json("memory/product_manager.json")
    arch_data = load_json("memory/architect_agent.json")
except FileNotFoundError as e:
    print(f"Error: {e}")
    print("Please make sure you have run the Product Manager and Architect agents first.")
    exit(1)

# Create a state dictionary matching the MultiAgent TypedDict schema
initial_state: MultiAgent = {
    "user_requirements": "Test requirement",
    "prd": prd_data,
    "architecture": arch_data,
    "code_manifest": {},
    "qa_report": {},
    "retry_count": 0,
    "status": {}
}

print("Running developer agent...")
# Pass the dictionary, not the Type class
developer_update = developer_agent(initial_state)
print("\nReturned State Update from Developer:")
print(developer_update)

# Update the state with developer results
initial_state.update(developer_update)

print("\nRunning QA agent...")
qa_update = qa_agent(initial_state)
print("\nReturned State Update from QA:")
print(qa_update)
