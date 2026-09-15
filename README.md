# AgriDirect

AgriDirect is an agricultural marketplace that connects farmers directly with customers.

## Project structure

- `frontend/` - React + Vite single-page web app
- `backend/` - Flask REST API server
- `database/` - MySQL schema and setup scripts
- `uploads/` - local image uploads

## Phase 1 status

This phase sets up the project structure, frontend app shell, backend API skeleton, database schema, and environment configuration. The frontend and backend are both runnable and verified.

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

## Features in this phase

- React + Vite frontend shell
- Flask backend health endpoint
- Bootstrap folder structure
- MySQL schema template for the marketplace
- Local uploads directory
- Developer documentation

## Notes

This is Phase 1 only. The full marketplace features are planned for later phases.
