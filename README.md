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

The program requirement checker page is at http://127.0.0.1:8000/planner/ (files live under [`planner/web/`](planner/web/)).

## Endpoints

- `GET /health` — Health check
- `GET /programs/by-id?catoid=21&poid=29994` — Get program by catalogue and program ID
- `POST /programs/evaluate?catoid=&poid=` — Body `{"taken":["CSCI 103L",...]}`: advisory progress vs program + GE listing (evaluation logic in [`planner/requirement_eval.py`](planner/requirement_eval.py))
- `GET /programs/{slug}` — Get program by slug (e.g. `csci-bs`)
- `GET /programs/{slug}/summary` — Summary (total units, course counts)
- `GET /ge/by-id?catoid=21&poid=29462` — General Education course listings (by catalogue + GE program id)

Query params: `force_refresh=true` to bypass cache.

## Tests

```bash
pip install pytest pytest-asyncio
pytest -v
```

## Configuration

Environment variables (optional):

- `CATALOGUE_BASE_URL` — Base URL (default: https://catalogue.usc.edu)
- `HTTP_TIMEOUT_SECONDS` — Request timeout (default: 30)
- `CACHE_TTL_SECONDS` — Cache TTL (default: 3600)
