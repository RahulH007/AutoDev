from prompts.architect_json_prompt import get_architect_prompt
from utils.json_utils import save_llm_json
from utils.llm_client import get_structured_llm, llm_call
from schema.architect_schema import ArchitectSchema
from prompts.architect_pdf_prompt import get_architecture_doc_prompt
from utils.pdf_util import save_to_pdf
architecture_model = get_structured_llm(ArchitectSchema)
from state.state import MultiAgent

def architecture_agent(state : MultiAgent):

    user_input = state['user_requirements']
    pd_json = state['prd'] 

    json_prompt = get_architect_prompt(user_input, pd_json)
    json_response = architecture_model.invoke(json_prompt)
    architect_json = json_response.model_dump()

    save_llm_json(architect_json, "architect_agent.json", folder="memory")

    pdf_prompt = get_architecture_doc_prompt(user_input, architect_json)
    pdf_response = llm_call(pdf_prompt)
    save_to_pdf(pdf_response, "architecture_doc.pdf", folder="memory")

    return {
        "architecture": json_response.model_dump(),
    }




