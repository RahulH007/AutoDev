# SpendWise Expense Tracker

SpendWise is a lightweight financial management tool for tracking daily expenditures and visualizing spending habits.

## Architecture Summary
- **Backend**: FastAPI (Python) providing a RESTful API.
- **Frontend**: React (Vite) with Tailwind CSS and Recharts.
- **Database**: PostgreSQL for persistent storage.
- **Auth**: JWT-based authentication.

## Prerequisites
- Python 3.9+
- Node.js 16+
- PostgreSQL

## Setup Instructions

### Backend
1. `cd backend`
2. `python -m venv venv`
3. `source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
4. `pip install -r requirements.txt`
5. Set environment variables:
   - `DATABASE_URL=postgresql://user:pass@localhost:5432/spendwise`
   - `JWT_SECRET=your_secret_key`
6. `uvicorn app.main:app --reload`

### Frontend
1. `cd frontend`
2. `npm install`
3. Create a `.env` file: `VITE_API_URL=http://localhost:8000/api/v1`
4. `npm run dev`

## API Endpoints
- `POST /api/v1/auth/register`: Register user
- `POST /api/v1/auth/login`: Login and get token
- `GET /api/v1/expenses`: List user expenses
- `POST /api/v1/expenses`: Create expense
- `GET /api/v1/reports/summary`: Monthly spending summary

## Development Notes
- Default categories are seeded on startup.
- Ensure PostgreSQL is running before starting the backend.