from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, datetime
from uuid import UUID

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class ExpenseCreate(BaseModel):
    amount: float
    category_id: int
    description: Optional[str] = None
    transaction_date: date

class ExpenseOut(BaseModel):
    id: UUID
    amount: float
    category_id: int
    description: Optional[str]
    transaction_date: date
    class Config:
        from_attributes = True

class CategoryOut(BaseModel):
    id: int
    name: str
    class Config:
        from_attributes = True

class ReportSummary(BaseModel):
    total: float
    breakdown: List[dict]