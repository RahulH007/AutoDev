from pydantic import BaseModel
from typing import List

class ManagerSchema(BaseModel):
    # Product Overview
    product_name: str
    product_summary: str
    problem_statement: str
    target_users: List[str]

    # Features
    core_features: List[str]
    mvp_features: List[str]

    # Feature → module hints for architect
    modules: List[str]

    # Database entities
    data_entities: List[str]

    # Candidate API endpoints
    possible_apis: List[str]

    # User interaction flows

    # Requirements
    functional_requirements: List[str]
    non_functional_requirements: List[str]

    # System constraints
    constraints: List[str]
    assumptions: List[str]

