# FactoryOS

FactoryOS is a browser-based factory operations demo. It generates repeatable factory scenarios from YAML, stores operational records and hidden evaluation truth, and serves a dashboard with evidence-based investigations.

## Run locally

The default database is a local SQLite file at `data/factoryos.db`, so the demo starts without a separate database service.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Open <http://127.0.0.1:8000>. API documentation is at <http://127.0.0.1:8000/docs>.

To use PostgreSQL, start the included database and set the connection string before starting the API:

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg2://factoryos:factoryos_dev@localhost:5432/factoryos"
uvicorn backend.app.main:app --reload
```

The simulator reads every `config/factories/*.yaml` file and creates 14 days of repeatable production records. Sample factories cover machine failure, material shortage, quality issue, supplier delay, overdue maintenance, operator shortage, and power outage. Each incident changes related production, downtime, quality, inventory, purchase-order, shift, or maintenance records. The ground-truth table is reserved for evaluation and is intentionally excluded from user-facing APIs.

## Current slice

- YAML-configurable multi-factory generator and correlated incident engine (`backend/app/simulator.py`)
- SQLAlchemy data model for factories, lines, machines, products, materials, suppliers, shifts, purchase orders, production, downtime, quality, maintenance, incidents, and hidden incident truth
- FastAPI endpoints for factory selection, operational records, investigations, and saved traces
- Browser dashboard served by FastAPI (`frontend/`)
- SQLite for zero-setup local use; PostgreSQL via `DATABASE_URL`

## Ask an investigation question

Use the investigation panel on the dashboard or call `POST /api/agent/query` with `{"factory_id":"F001","question":"Why did production drop on line F001-L02?"}`. Every task records its evidence items, status, and tool trace. Retrieve a saved trace from `GET /api/agent/task/{task_id}/trace`.

Without model credentials, the harness runs a deterministic investigation through the same read-only tools. It correlates the simulated downtime with production, quality, maintenance, and manual evidence and labels the response as rule-based. To enable an OpenAI-compatible tool-calling model, set `LLM_API_KEY` and `LLM_MODEL`; optionally change `LLM_API_BASE_URL` for another compatible provider. The model can only call the allowlisted read-only tools in `backend/app/tools.py`. Its factual answer must cite evidence IDs; the output guardrail replaces uncited or invalidly cited text with the evidence-based summary.

For a PowerShell session, set `$env:LLM_API_KEY` and `$env:LLM_MODEL` before starting Uvicorn. The backend uses those variables only when both are set; otherwise it runs rule-based mode.

Factory documents are chunked and indexed automatically for lexical retrieval at startup. To create vector embeddings, set `$env:LLM_API_KEY` and `$env:LLM_EMBEDDING_MODEL`, then run `python -m backend.app.ingest_documents --embeddings`. Vectors are stored with document metadata and ranked by cosine similarity. This first vector store uses the configured SQL database and Python similarity ranking; pgvector indexing and reranking are still future work.

## Access control

For a single-operator password gate, set `FACTORYOS_ACCESS_PASSWORD` and a long random `FACTORYOS_SESSION_SECRET` before starting the server. The dashboard then shows a sign-in screen; successful sign-in uses an eight-hour HttpOnly, SameSite=Strict cookie. Set `FACTORYOS_COOKIE_SECURE=true` when serving through HTTPS. If the password and session secret are unset, local development runs without login.

## Evaluate scenarios

`evals/benchmark.yaml` covers the seven seeded incident scenarios. Run `python -m evals.run` to compare the deterministic investigation against hidden simulator truth, citation coverage, and evidence traces. Add `--factory F003` to run one case. The evaluator reads ground truth; the user-facing agent and API do not.

Approval workflows and specialist multi-agent orchestration remain future work. The agent stays read-only until an approval workflow is implemented.
