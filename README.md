# AgriDirect

AgriDirect is an agricultural marketplace that connects farmers directly with customers.

## Project structure

- `frontend/` - React + Vite single-page web app
- `backend/` - Flask REST API server
- `database/` - MySQL schema and setup scripts
- `uploads/` - local image uploads

## Current status

The project includes a React/Vite frontend, Flask REST API, JWT authentication, seeded marketplace data, customer cart and checkout, and role-based customer, farmer, and admin dashboards.

## Quick start

### 1) Frontend

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

### 2) Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

### 3) Database

Create a MySQL database and run:

```sql
SOURCE database/schema.sql;
```

## Environment variables

Copy `backend/.env.example` to `backend/.env` and update values for your local MySQL configuration.

## Included features

- React + Vite responsive frontend
- Flask REST API with JWT authentication
- SQLite development database with MySQL schema template
- Product catalog, cart, COD checkout, and order creation
- Customer, farmer, and admin dashboard statistics
- Local uploads directory and environment configuration
- Developer documentation

## Notes

SQLite is used for the zero-setup local development run. The relational MySQL schema is provided in `database/schema.sql` for a production database migration.
