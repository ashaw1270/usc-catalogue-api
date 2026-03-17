# USC Catalogue API

API for retrieving course requirements for USC majors and minors from [catalogue.usc.edu](https://catalogue.usc.edu/). Data is scraped on demand and cached in memory.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/docs for the interactive API docs.

## Endpoints

- `GET /health` — Health check
- `GET /programs/by-id?catoid=21&poid=29994` — Get program by catalogue and program ID
- `GET /programs/{slug}` — Get program by slug (e.g. `csci-bs`)
- `GET /programs/{slug}/summary` — Summary (total units, course counts)

Query params: `force_refresh=true` to bypass cache.

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Configuration

Environment variables (optional):

- `CATALOGUE_BASE_URL` — Base URL (default: https://catalogue.usc.edu)
- `HTTP_TIMEOUT_SECONDS` — Request timeout (default: 30)
- `CACHE_TTL_SECONDS` — Cache TTL (default: 3600)
