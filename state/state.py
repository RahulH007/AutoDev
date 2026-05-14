from typing import TypedDict, Dict, Any

class MultiAgent(TypedDict):
    user_requirements: str
    prd: Dict[str, Any]
    architecture: Dict[str, Any]
    code_manifest: Dict[str, Any]
    qa_report: Dict[str, Any]
    retry_count: int
    status: Dict[str, str]