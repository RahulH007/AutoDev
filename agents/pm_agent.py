from prompts.pm_prompt import get_pm_prompt
from utils.json_utils import save_llm_json
from utils.llm_client import get_structured_llm
from state.state import MultiAgent
from schema.product_manager_schema import ManagerSchema
from utils.pdf_util import save_pdf

pm_model = get_structured_llm(ManagerSchema)

def pm_agent(state: MultiAgent) -> MultiAgent:

    user_input = state['user_requirements']

    prompt = get_pm_prompt(user_input)

    response = pm_model.invoke(prompt)

    prd_dict = response.model_dump()

    # save JSON
    save_llm_json(prd_dict, "product_manager.json", folder="memory")

    # save PDF
    save_pdf(prd_dict, "product_manager.pdf", folder="memory") 

    return {
        "prd": prd_dict,
    }
