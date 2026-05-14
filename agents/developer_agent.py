import os
import traceback
from langchain_core.exceptions import OutputParserException

from prompts.developer_json_prompt import get_developer_prompt
from prompts.developer_pdf_prompt import get_developer_doc_prompt
from utils.json_utils import save_llm_json
from utils.llm_client import get_structured_llm, llm_call
from state.state import MultiAgent
from schema.developer_schema import DeveloperSchema
from utils.pdf_util import save_to_pdf
from utils.status_tracker import print_status_banner

# Initialize the structured LLM
developer_model = get_structured_llm(DeveloperSchema)

def developer_agent(state: MultiAgent):
    
    # Update status to in progress
    if "status" not in state:
        state["status"] = {}
    state["status"]["developer_agent"] = "IN_PROGRESS"
    
    # Increment retry count
    retry_count = state.get("retry_count", 0) + 1
    state["retry_count"] = retry_count
    
    print_status_banner(state)

    user_input = state.get('user_requirements', '')
    prd_json = state.get('prd', {})
    architect_json = state.get('architecture', {})
    qa_report = state.get('qa_report')

    json_prompt = get_developer_prompt(user_input, prd_json, architect_json, qa_report)
    
    # ==========================================
    # ERROR HANDLING BLOCK FOR JSON PARSING
    # ==========================================
    try:
        json_response = developer_model.invoke(json_prompt)
        developer_dict = json_response.model_dump()
        
    except (OutputParserException, ValueError) as e:
        print(f"\n[ERROR] LLM failed to output valid JSON on attempt {retry_count}.")
        print(f"Error details: {e}")
        
        # Mark status as failed so LangGraph can route it to a retry node/edge
        state["status"]["developer_agent"] = "FAILED"
        return {
            "status": state["status"],
            "retry_count": retry_count
        }
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Unexpected failure in developer_agent: {e}")
        traceback.print_exc()
        state["status"]["developer_agent"] = "FAILED"
        return {
            "status": state["status"],
            "retry_count": retry_count
        }
    # ==========================================

    # Save the successful JSON response
    save_llm_json(developer_dict, "developer_agent.json", folder="memory")

    # Generate PDF
    pdf_prompt = get_developer_doc_prompt(user_input, developer_dict)
    pdf_response = llm_call(pdf_prompt)
    save_to_pdf(pdf_response, "developer_doc.pdf", folder="memory")

    # Write files to outputs/source_code and build manifest
    base_dir = "outputs/source_code"
    os.makedirs(base_dir, exist_ok=True)
    
    # We will update the manifest, not overwrite it, so files from previous 
    # runs aren't lost if they weren't regenerated this time.
    manifest = state.get("code_manifest", {})

    for service in developer_dict.get("services", []):
        service_name = service.get("service_name", "unnamed_service")
        service_dir = os.path.join(base_dir, service_name)
        os.makedirs(service_dir, exist_ok=True)
        
        for file in service.get("files", []):
            file_path = file.get("file_path")
            if not file_path:
                continue
            
            # Remove any leading slashes to prevent absolute path issues
            if file_path.startswith('/'):
                file_path = file_path[1:]
                
            full_path = os.path.join(service_dir, file_path)
            
            # Create directories if they don't exist
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(file.get("code", ""))
                
            # Add to manifest
            if service_name not in manifest:
                manifest[service_name] = []
            
            # Avoid duplicates in manifest
            existing_files = [m["file_path"] for m in manifest[service_name]]
            if file_path not in existing_files:
                manifest[service_name].append({
                    "file_path": file_path,
                    "description": file.get("description", "")
                })

    # Write dependency files (requirements.txt, package.json, etc.) to service dirs or root
    for dep_file in developer_dict.get("dependency_files", []):
        file_path = dep_file.get("file_path", "")
        if not file_path:
            continue
        if file_path.startswith('/'):
            file_path = file_path[1:]
        full_path = os.path.join(base_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(dep_file.get("code", ""))
        print(f"✅ Saved dependency file to {full_path}")

    # Write README.md to the root of outputs/source_code
    readme_content = developer_dict.get("readme_content", "")
    if readme_content:
        readme_path = os.path.join(base_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_content)
        print(f"✅ Saved README.md to {readme_path}")

    # Update status to completed
    state["status"]["developer_agent"] = "COMPLETED"

    return {
        "code_manifest": manifest,
        "retry_count": retry_count,
        "status": state["status"]
    }