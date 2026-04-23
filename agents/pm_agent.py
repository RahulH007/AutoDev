from prompts.pm_json_prompt import get_pm_prompt
from prompts.pm_pdf_prompt import get_pm_doc_prompt
from utils.json_utils import save_llm_json
from utils.llm_client import get_structured_llm, llm_call
from state.state import MultiAgent
from schema.product_manager_schema import ManagerSchema
from utils.pdf_util import save_to_pdf 


pm_model = get_structured_llm(ManagerSchema)

def pm_agent(state: MultiAgent):

    user_input = state['user_requirements']

    prompt = get_pm_prompt(user_input)

    json_response = pm_model.invoke(prompt)

    prd_dict = json_response.model_dump()

    pdf_response = llm_call(get_pm_doc_prompt(user_input , prd_dict)  )

    # save JSON
    save_llm_json(prd_dict, "product_manager.json", folder="memory")

    # save PDF
    save_to_pdf(pdf_response, "product_manager.pdf", folder="memory") 

    return {
        "prd": prd_dict,
    }

