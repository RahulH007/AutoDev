from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import auth, expenses, reports
from app.core.database import engine, Base
from app.models import models
from sqlalchemy.orm import Session
from app.core.database import SessionLocal

# Create tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="SpendWise API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Seed categories on startup
@app.on_event("startup")
def seed_categories():
    db = SessionLocal()
    categories = ["Food", "Transport", "Rent", "Utilities", "Entertainment"]
    for cat_name in categories:
        exists = db.query(models.Category).filter(models.Category.name == cat_name).first()
        if not exists:
            db.add(models.Category(name=cat_name, icon_identifier=cat_name.lower()))
    db.commit()
    db.close()

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(expenses.router, prefix="/api/v1/expenses", tags=["expenses"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])

@app.get("/")
def read_root():
    return {"message": "Welcome to SpendWise API"}