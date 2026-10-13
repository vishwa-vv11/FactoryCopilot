"""Read-only HTTP API for the V2 simulator's local files.

From the workspace root, run with:

    python -m uvicorn api:app --app-dir v2 --reload --port 8001

Interactive API documentation is available at ``http://127.0.0.1:8001/docs``.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


DATA_DIR = Path(__file__).resolve().parent / "data" / "factories"
WEB_DIR = Path(__file__).resolve().parent / "frontend"
FACTORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

app = FastAPI(
    title="FactoryOS V2 Local API",
    version="0.1.0",
    description="Read-only API for locally simulated factory operations data.",
)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


def normalize_factory_id(factory_id: str) -> str:
    cleaned = str(factory_id).strip()
    if cleaned.isdigit():
        return f"F{int(cleaned):03d}"
    if cleaned.upper().startswith("F") and cleaned[1:].isdigit():
        return f"F{int(cleaned[1:]):03d}"
    return cleaned.upper()


def _factory_dir(factory_id: str) -> Path:
    fid = normalize_factory_id(factory_id)
    if not FACTORY_ID_PATTERN.fullmatch(fid):
        raise HTTPException(status_code=400, detail="Invalid factory ID")
    matches = [path for path in DATA_DIR.glob(f"{fid}-*") if path.is_dir()]
    if not matches:
        matches = [path for path in DATA_DIR.glob(f"{factory_id}-*") if path.is_dir()]
    if not matches:
        raise HTTPException(status_code=404, detail="Factory not found; run the simulator first")
    return matches[0]


def _day_dir(factory_id: str, day: str | None) -> tuple[date, Path]:
    selected_day = date.fromisoformat(day) if day else datetime.now().astimezone().date()
    folder = _factory_dir(factory_id) / selected_day.isoformat()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"No simulator data for {selected_day.isoformat()}")
    return selected_day, folder


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "data_dir": str(DATA_DIR), "data_dir_exists": DATA_DIR.exists()}


@app.get("/api/factories")
def factories() -> list[dict]:
    if not DATA_DIR.exists():
        return []
    result = []
    for path in sorted(DATA_DIR.glob("*/factory.json")):
        result.append(json.loads(path.read_text(encoding="utf-8")))
    return result


@app.get("/api/factories/{factory_id}")
def factory(factory_id: str) -> dict:
    return _read_json(_factory_dir(factory_id) / "factory.json")


@app.get("/api/factories/{factory_id}/days")
def factory_days(factory_id: str) -> list[str]:
    folder = _factory_dir(factory_id)
    days = []
    for child in folder.iterdir():
        if child.is_dir():
            try:
                days.append(date.fromisoformat(child.name).isoformat())
            except ValueError:
                continue
    return sorted(days)


@app.get("/api/factories/{factory_id}/materials")
def materials(factory_id: str) -> list[dict]:
    payload = _read_json(_factory_dir(factory_id) / "materials.json")
    return payload if isinstance(payload, list) else []


@app.get("/api/factories/{factory_id}/bill-of-materials")
def bill_of_materials(factory_id: str) -> list[dict]:
    return _read_csv(_factory_dir(factory_id) / "bill_of_materials.csv")


@app.get("/api/factories/{factory_id}/production")
def production(factory_id: str, day: str | None = Query(default=None, description="Local factory date in YYYY-MM-DD format")) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "lines": _read_csv(folder / "production.csv")}


@app.get("/api/factories/{factory_id}/comparison")
def production_comparison(
    factory_id: str,
    day: str | None = Query(default=None, description="Date to compare, in YYYY-MM-DD format"),
    compare_to: str | None = Query(default=None, description="Comparison date; defaults to the previous calendar day"),
) -> dict:
    current_day = date.fromisoformat(day) if day else datetime.now().astimezone().date()
    prior_day = date.fromisoformat(compare_to) if compare_to else current_day - timedelta(days=1)
    current_folder = _factory_dir(factory_id) / current_day.isoformat()
    prior_folder = _factory_dir(factory_id) / prior_day.isoformat()
    if not current_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"No simulator data for {current_day.isoformat()}")

    current_rows = _read_csv(current_folder / "production.csv")
    prior_rows = _read_csv(prior_folder / "production.csv") if prior_folder.is_dir() else []
    current_by_line = {row["line_id"]: row for row in current_rows}
    prior_by_line = {row["line_id"]: row for row in prior_rows}
    lines = []
    for line_id, row in current_by_line.items():
        current_units = int(row["achieved_units"])
        previous = prior_by_line.get(line_id)
        prior_units = int(previous["achieved_units"]) if previous else None
        delta = current_units - prior_units if prior_units is not None else None
        delta_pct = round(delta / prior_units * 100, 1) if prior_units else None
        lines.append({
            "line_id": line_id,
            "line_code": row.get("line_code"),
            "line_name": row.get("line_name"),
            "product_name": row.get("product_name"),
            "current_date": current_day.isoformat(),
            "current_achieved_units": current_units,
            "comparison_date": prior_day.isoformat(),
            "comparison_achieved_units": prior_units,
            "change_units": delta,
            "change_percent": delta_pct,
        })
    return {
        "factory_id": factory_id,
        "has_comparison_data": bool(prior_rows),
        "lines": lines,
        "current_factory_total": sum(int(row["achieved_units"]) for row in current_rows),
        "comparison_factory_total": sum(int(row["achieved_units"]) for row in prior_rows) if prior_rows else None,
    }


@app.get("/api/factories/{factory_id}/inventory")
def inventory(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "materials": _read_csv(folder / "inventory.csv")}


@app.get("/api/factories/{factory_id}/deliveries")
def deliveries(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "deliveries": _read_jsonl(folder / "deliveries.jsonl")}


@app.get("/api/factories/{factory_id}/machines")
def machines(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    snapshot = _read_json(folder / "machine_status.json")
    return {"factory_id": factory_id, "date": selected_day.isoformat(), **snapshot}


@app.get("/api/factories/{factory_id}/machine-logs")
def machine_logs(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "events": _read_jsonl(folder / "machine_logs.jsonl")}


@app.get("/api/factories/{factory_id}/downtime")
def downtime(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "events": _read_csv(folder / "downtime.csv")}


@app.get("/api/factories/{factory_id}/quality")
def quality(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "lines": _read_csv(folder / "quality.csv")}


@app.get("/api/factories/{factory_id}/quality-events")
def quality_events(factory_id: str, day: str | None = Query(default=None)) -> dict:
    selected_day, folder = _day_dir(factory_id, day)
    return {"factory_id": factory_id, "date": selected_day.isoformat(), "events": _read_jsonl(folder / "quality_events.jsonl")}


@app.get("/api/factories/{factory_id}/shortfall-analysis")
def shortfall_analysis(
    factory_id: str,
    day: str | None = Query(default=None),
    compare_to: str | None = Query(default=None),
) -> dict:
    """Summarize observed shortfall signals with their source records."""
    selected_day, folder = _day_dir(factory_id, day)
    factory_folder = _factory_dir(factory_id)
    metadata = _read_json(factory_folder / "factory.json")
    production_rows = _read_csv(folder / "production.csv")
    comparison_day = date.fromisoformat(compare_to) if compare_to else selected_day - timedelta(days=1)
    comparison_folder = factory_folder / comparison_day.isoformat()
    comparison_rows = _read_csv(comparison_folder / "production.csv") if comparison_folder.is_dir() else []
    comparison_by_line = {row.get("line_id"): row for row in comparison_rows}
    downtime_rows = _read_csv(folder / "downtime.csv")
    quality_rows = _read_csv(folder / "quality.csv")
    inventory_rows = _read_csv(folder / "inventory.csv")
    bom_rows = _read_csv(factory_folder / "bill_of_materials.csv")
    machine_rows = _read_json(folder / "machine_status.json").get("machines", [])

    downtime_by_line: dict[str, list[dict]] = {}
    for event in downtime_rows:
        downtime_by_line.setdefault(event.get("line_id", ""), []).append(event)
    quality_by_line = {row.get("line_id"): row for row in quality_rows}
    inventory_by_material = {row.get("material_code"): row for row in inventory_rows}
    bom_by_line: dict[str, list[dict]] = {}
    for row in bom_rows:
        bom_by_line.setdefault(row.get("line_id", ""), []).append(row)
    down_machines_by_line: dict[str, list[dict]] = {}
    for machine in machine_rows:
        if str(machine.get("status", "")).lower() == "down":
            down_machines_by_line.setdefault(machine.get("line_id", ""), []).append(machine)

    results = []
    for row in production_rows:
        line_id = row.get("line_id", "")
        target = int(row.get("target_units") or 0)
        achieved = int(row.get("achieved_units") or 0)
        shortfall = max(0, int(row.get("shortfall_units") or target - achieved))
        factors = []
        if shortfall:
            completed_stops = downtime_by_line.get(line_id, [])
            current_stops = down_machines_by_line.get(line_id, [])
            if completed_stops or current_stops:
                minutes = sum(int(event.get("duration_minutes") or 0) for event in completed_stops)
                factors.append({
                    "type": "downtime", "label": "Machine downtime observed",
                    "detail": f"{len(completed_stops)} completed stop(s), {minutes} recorded minutes"
                    + (f", {len(current_stops)} machine(s) currently down" if current_stops else ""),
                    "evidence": [{"source": "downtime.csv", "machine_id": event.get("machine_id"),
                                  "duration_minutes": int(event.get("duration_minutes") or 0),
                                  "reason": event.get("reason")} for event in completed_stops]
                    + [{"source": "machine_status.json", "machine_id": machine.get("machine_id"),
                        "status": machine.get("status")} for machine in current_stops],
                })

            if row.get("line_status") == "material_shortage":
                materials = []
                for requirement in bom_by_line.get(line_id, []):
                    stock = inventory_by_material.get(requirement.get("material_code"))
                    if stock and stock.get("status") in {"reorder", "out_of_stock"}:
                        materials.append({"material_code": stock.get("material_code"),
                                          "material_name": stock.get("material_name"),
                                          "available_quantity": stock.get("available_quantity"),
                                          "unit": stock.get("unit"), "status": stock.get("status")})
                factors.append({
                    "type": "materials", "label": "Simulator marked a material shortage",
                    "detail": "Production was constrained by available bill-of-material inputs.",
                    "evidence": [{"source": "production.csv", "line_status": row.get("line_status")}, *materials],
                })

        quality = quality_by_line.get(line_id)
        quality_loss = int(quality.get("rework_units") or 0) + int(quality.get("rejected_units") or 0) if quality else 0
        if quality_loss:
            factors.append({
                "type": "quality", "label": "Quality loss recorded",
                "detail": f"{int(quality.get('rework_units') or 0)} unit(s) need rework and {int(quality.get('rejected_units') or 0)} were rejected.",
                "evidence": [{"source": "quality.csv", "inspected_units": int(quality.get("inspected_units") or 0),
                              "rework_units": int(quality.get("rework_units") or 0),
                              "rejected_units": int(quality.get("rejected_units") or 0),
                              "yield_pct": float(quality.get("yield_pct") or 0)}],
            })

        previous = comparison_by_line.get(line_id)
        previous_achieved = int(previous["achieved_units"]) if previous else None
        results.append({
            "line_id": line_id, "line_name": row.get("line_name"),
            "product_name": row.get("product_name"), "target_units": target,
            "achieved_units": achieved, "shortfall_units": shortfall,
            "attainment_pct": float(row.get("attainment_pct") or 0),
            "line_status": row.get("line_status"),
            "comparison_date": comparison_day.isoformat(),
            "comparison_achieved_units": previous_achieved,
            "change_from_comparison": achieved - previous_achieved if previous_achieved is not None else None,
            "assessment": "Target met" if not shortfall else (
                "Evidence signals found" if factors else "Shortfall recorded; no matching machine, material, or quality signal found"
            ),
            "factors": factors,
        })

    return {"factory_id": factory_id, "factory_name": metadata.get("name"),
            "date": selected_day.isoformat(), "comparison_date": comparison_day.isoformat(),
            "has_comparison_data": bool(comparison_rows),
            "method": "deterministic evidence correlation",
            "lines": results}


from pydantic import BaseModel, Field


class AgentQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    factory_id: str = Field(default="F001", min_length=1, max_length=32)
    multi_agent: bool = Field(default=False)


@app.post("/api/agent/query")
def agent_query(payload: AgentQueryRequest) -> dict:
    """Run an AI investigation via single-agent harness or multi-agent supervisor."""
    if payload.multi_agent:
        from v2.multi_agent import SupervisorAgent
        supervisor = SupervisorAgent()
        result = supervisor.run(question=payload.question, factory_id=payload.factory_id)
        return result.to_dict()
    else:
        from v2.harness import AgentHarness
        harness = AgentHarness()
        result = harness.run(question=payload.question, factory_id=payload.factory_id)
        return result.to_dict()


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
