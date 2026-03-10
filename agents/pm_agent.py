from prompts.pm_prompt import get_pm_prompt
from utils.json_utils import save_llm_json
from utils.llm_client import llm_call
from state.state import MultiAgent

def pm_agent(state: MultiAgent) -> MultiAgent:

    user_input = state['user_requirements']

    prompt = get_pm_prompt(user_input)

    response = llm_call(prompt)

    cleaned_json = save_llm_json(response, "product_manager.json", folder="memory")    

    return {
        "prd": response,
    }
