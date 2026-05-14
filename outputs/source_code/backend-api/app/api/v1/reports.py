from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.api import deps
from app.models import models
from app.schemas import schemas
from datetime import datetime

router = APIRouter()

@router.get("/summary", response_model=schemas.ReportSummary)
def get_summary(db: Session = Depends(deps.get_db), current_user: models.User = Depends(deps.get_current_user)):
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    expenses = db.query(models.Expense).filter(
        models.Expense.user_id == current_user.id,
        func.extract('month', models.Expense.transaction_date) == current_month,
        func.extract('year', models.Expense.transaction_date) == current_year
    ).all()
    
    total = sum(e.amount for e in expenses)
    
    # Group by category
    breakdown_query = db.query(
        models.Category.name,
        func.sum(models.Expense.amount).label('amount')
    ).join(models.Expense).filter(
        models.Expense.user_id == current_user.id,
        func.extract('month', models.Expense.transaction_date) == current_month
    ).group_by(models.Category.name).all()
    
    breakdown = [{"category": row[0], "amount": row[1]} for row in breakdown_query]
    
    return {"total": total, "breakdown": breakdown}