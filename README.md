# CondoReadout

Recertification and risk report for condos in the City of Miami, built from public records.

| Folder | What it holds |
|---|---|
| `backend/` | Loaders (`load_*.py`, `nightly.py`), the report (`report.py`, `report_db.py`, `core.py`) and the web API (`api.py`) |
| `frontend/` | The web pages (React + Vite) |
| `db/` | The PostgreSQL schema and the nightly schedule |
| `experiment/` | Research scripts, not used by the application |

Run the API from `backend/` with `../.venv/bin/uvicorn api:app --reload`, and the web pages from
`frontend/` with `npm run dev`. Installation, data sources and decisions are in the
[wiki](https://github.com/piedraym/CondoReadout/wiki).
