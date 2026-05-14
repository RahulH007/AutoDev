import os
import json
from prompts.qa_json_prompt import get_qa_prompt
from prompts.qa_pdf_prompt import get_qa_doc_prompt
from utils.json_utils import save_llm_json
from utils.llm_client import get_structured_llm, llm_call
from state.state import MultiAgent
from schema.qa_schema import QASchema
from utils.pdf_util import save_to_pdf
from utils.status_tracker import print_status_banner

qa_model = get_structured_llm(QASchema)

def qa_agent(state: MultiAgent):
    
    # Update status
    if "status" not in state:
        state["status"] = {}
    state["status"]["qa_agent"] = "IN_PROGRESS"
    print_status_banner(state)

    prd_json = state['prd']
    architect_json = state['architecture']
    code_manifest = state.get('code_manifest', {})

    # Read the actual code from disk
    base_dir = "outputs/source_code"
    all_code_content = ""
    
    for service_name, files in code_manifest.items():
        all_code_content += f"\n\n{'='*50}\nSERVICE: {service_name}\n{'='*50}\n"
        service_dir = os.path.join(base_dir, service_name)
        
        for file_info in files:
            file_path = file_info["file_path"]
            full_path = os.path.join(service_dir, file_path)
            
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                all_code_content += f"\n--- FILE: {file_path} ---\n{content}\n"
            except Exception as e:
                all_code_content += f"\n--- FILE: {file_path} (ERROR READING FILE: {str(e)}) ---\n"

    # For very large projects, we might need to process service-by-service,
    # but for now we'll combine it since Gemini 2.5 has a large context window.
    
    json_prompt = get_qa_prompt(prd_json, architect_json, code_manifest, all_code_content)
    json_response = qa_model.invoke(json_prompt)
    qa_dict = json_response.model_dump()

    save_llm_json(qa_dict, "qa_agent.json", folder="memory")

    # Generate PDF
    pdf_prompt = get_qa_doc_prompt(qa_dict)
    pdf_response = llm_call(pdf_prompt)
    save_to_pdf(pdf_response, "qa_report.pdf", folder="memory")

    # Write test files to outputs/tests
    tests_dir = "outputs/tests"
    os.makedirs(tests_dir, exist_ok=True)
    
    for service_report in qa_dict.get("service_reports", []):
        service_name = service_report.get("service_name", "unnamed_service")
        service_tests_dir = os.path.join(tests_dir, service_name)
        os.makedirs(service_tests_dir, exist_ok=True)
        
        for test_case in service_report.get("test_cases", []):
            test_path = test_case.get("test_file_path")
            if not test_path:
                continue
                
            # Remove any leading slashes
            if test_path.startswith('/'):
                test_path = test_path[1:]
                
            full_path = os.path.join(service_tests_dir, test_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(test_case.get("test_code", ""))

    # Update status to completed
    state["status"]["qa_agent"] = "COMPLETED"

    return {
        "qa_report": qa_dict,
        "status": state["status"]
    }
