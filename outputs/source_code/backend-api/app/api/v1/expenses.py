from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.api import deps
from app.models import models
from app.schemas import schemas

router = APIRouter()

@router.get("/", response_model=List[schemas.ExpenseOut])
def get_expenses(category_id: Optional[int] = None, db: Session = Depends(deps.get_db), current_user: models.User = Depends(deps.get_current_user)):
    query = db.query(models.Expense).filter(models.Expense.user_id == current_user.id)
    if category_id:
        query = query.filter(models.Expense.category_id == category_id)
    return query.all()

@router.post("/", response_model=schemas.ExpenseOut)
def create_expense(expense_in: schemas.ExpenseCreate, db: Session = Depends(deps.get_db), current_user: models.User = Depends(deps.get_current_user)):
    new_expense = models.Expense(
        **expense_in.dict(),
        user_id=current_user.id
    )
    db.add(new_expense)
    db.commit()
    db.refresh(new_expense)
    return new_expense

@router.delete("/{expense_id}")
def delete_expense(expense_id: str, db: Session = Depends(deps.get_db), current_user: models.User = Depends(deps.get_current_user)):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id, models.Expense.user_id == current_user.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    db.delete(expense)
    db.commit()
    return {"status": "deleted"}