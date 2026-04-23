from typing import TypedDict
from schema.product_manager_schema import ManagerSchema
from schema.architect_schema import ArchitectSchema


class MultiAgent(TypedDict):
    user_requirements: str
    prd: ManagerSchema
    architecture    : ArchitectSchema