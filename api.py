"""Read-only HTTP API for the V2 simulator's local files.

From the workspace root, run with:

    python -m uvicorn api:app --app-dir v2 --reload --port 8001

Interactive API documentation is available at ``http://127.0.0.1:8001/docs``.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
if str(_THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


from contextlib import asynccontextmanager
import threading

DATA_DIR = Path(__file__).resolve().parent / "data" / "factories"
WEB_DIR = Path(__file__).resolve().parent / "frontend"
FACTORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_sim_stop_event = threading.Event()
_sim_thread: threading.Thread | None = None
_sim_instance = None

def _run_background_simulator():
    global _sim_instance
    try:
        from v2.simulator import FactorySimulator
    except ModuleNotFoundError:
        from simulator import FactorySimulator

    sim = FactorySimulator()
    _sim_instance = sim
    sim.write_factory_metadata()
    today = datetime.now().astimezone().date()
    sim._read_today_totals(today)
    sim.simulated_now = datetime.combine(today, time(hour=10, minute=0), tzinfo=timezone.utc)
    sim.active_day = today
    # shortcut: static temp bottleneck on L01-M03 operator absence for 1/1 workstation demonstration, remove when live staffing scenario is configured
    sim.status[("F001", "L01-M01")] = "running"
    sim.status[("F001", "L01-M03")] = "running"
    sim.status[("F001", "L01-M04")] = "running"
    sim.status[("F001", "L01-M06")] = "running"
    print(f"[Simulator Lifespan] Background factory simulator started for {today}. Ticking every 5s...")
    while not _sim_stop_event.is_set():
        try:
            sim.tick()
        except Exception as e:
            print(f"[Simulator Lifespan Error] {e}")
        _sim_stop_event.wait(5.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sim_thread
    _sim_stop_event.clear()
    _sim_thread = threading.Thread(target=_run_background_simulator, daemon=True, name="FactorySimulatorDaemon")
    _sim_thread.start()
    yield
    _sim_stop_event.set()
    if _sim_thread and _sim_thread.is_alive():
        _sim_thread.join(timeout=2.0)

app = FastAPI(
    title="FactoryOS V2 Local API",
    version="0.1.0",
    description="Read-only API for locally simulated factory operations data with integrated live simulation.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")




@app.get("/api/simulator/status")
def simulator_status() -> dict:
    return {
        "running": _sim_thread is not None and _sim_thread.is_alive(),
        "tick_interval_seconds": 5,
        "tick_number": _sim_instance.tick_number if _sim_instance else 0,
        "mode": "in-process background daemon"
    }


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
        raise HTTPException(status_code=404, detail="Factory not found; run the simulator first")
    return matches[0]


def _day_dir(factory_id: str, day: str | None) -> tuple[date, Path]:
    try:
        selected_day = date.fromisoformat(day) if day else datetime.now().astimezone().date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format; expected YYYY-MM-DD")
    folder = _factory_dir(factory_id) / selected_day.isoformat()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"No simulator data for {selected_day.isoformat()}")
    return selected_day, folder


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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


class DispatchOrderRequest(BaseModel):
    line_id: str = Field(min_length=2, max_length=16)
    order_id: str | None = None


@app.get("/api/factories/{factory_id}/orders")
def factory_orders(
    factory_id: str,
    line_id: str | None = None,
    status: str | None = None,
    day: str | None = None,
) -> dict:
    folder = _factory_dir(factory_id)
    orders = _read_json(folder / "orders.json")
    if not isinstance(orders, list):
        return {"orders": [], "active_orders": {}, "next_orders": {}}

    filtered = []
    for o in orders:
        if line_id and str(o.get("line_id", "")).upper() != line_id.upper():
            continue
        if status and str(o.get("status", "")).lower() != status.lower():
            continue
        filtered.append(o)

    completed_orders: dict[str, dict] = {}
    active_orders: dict[str, dict] = {}
    next_orders: dict[str, dict] = {}

    prod_target_met: set[str] = set()
    active_batch_map: dict[str, int] = {}
    prod_targets: dict[str, int] = {}
    if day:
        cd = str(day).strip()
        day_prod_path = folder / cd / "production.csv"
        if day_prod_path.is_file():
            day_rows = _read_csv(day_prod_path)
            for r in day_rows:
                rlid = str(r.get("line_id", "")).upper()
                st = str(r.get("line_status", "")).lower()
                ach = float(r.get("achieved_units") or 0)
                tgt = float(r.get("target_units") or 0)
                prod_targets[rlid] = int(tgt)
                active_batch_map[rlid] = int(float(r.get("active_batch_units") or 0))
                if st == "target_met" or (tgt > 0 and ach >= tgt):
                    prod_target_met.add(rlid)

    line_map: dict[str, list[dict]] = {}
    for o in orders:
        lid = str(o.get("line_id", "")).upper()
        if lid not in line_map:
            line_map[lid] = []
        line_map[lid].append(o)

    for lid, l_orders in line_map.items():
        sorted_orders = sorted(l_orders, key=lambda x: (x.get("order_number") or 999, x.get("start_date") or ""))
        completed_order = None
        active_order = None

        cd = str(day).strip() if day else date.today().isoformat()
        # 1. First priority: An order currently explicitly marked in_production
        in_prod = [o for o in sorted_orders if str(o.get("status", "")).lower() == "in_production"]
        if in_prod:
            active_order = in_prod[0]
            curr_idx = sorted_orders.index(active_order)
            prior_completed = [o for o in sorted_orders[:curr_idx] if o.get("status") == "completed" and o.get("completion_date") == cd]
            if prior_completed:
                completed_orders[lid] = prior_completed[-1]
            if curr_idx + 1 < len(sorted_orders):
                next_orders[lid] = sorted_orders[curr_idx + 1]
        else:
            # 2. If no order is currently in_production, check if an order completed today
            comp_today = [o for o in sorted_orders if o.get("status") == "completed" and o.get("completion_date") == cd]
            if comp_today:
                completed_order = comp_today[-1]
                completed_orders[lid] = completed_order
                c_idx = sorted_orders.index(completed_order)
                if c_idx + 1 < len(sorted_orders):
                    active_order = sorted_orders[c_idx + 1]
                    if c_idx + 2 < len(sorted_orders):
                        next_orders[lid] = sorted_orders[c_idx + 2]
                else:
                    active_order = completed_order
            else:
                for o in sorted_orders:
                    s_date = o.get("start_date", "")
                    c_date = o.get("completion_date") or o.get("due_date", "9999-12-31")
                    if s_date and s_date <= cd <= c_date and o.get("status") != "completed":
                        active_order = o
                        break
                if not active_order and sorted_orders:
                    active_order = sorted_orders[0]

        if active_order:
            active_order = dict(active_order)
            if lid in active_batch_map:
                active_order["units_completed_current_shift"] = active_batch_map[lid]

            # Multi-day contract stats & schedule calculations
            tot = int(active_order.get("total_order_units") or 0)
            cum = int(active_order.get("units_completed") or 0)
            shift_ach = int(active_order.get("units_completed_current_shift") or 0)
            cum = max(cum, shift_ach)
            active_order["cumulative_units_completed"] = cum
            rem = max(0, tot - cum)
            active_order["remaining_order_units"] = rem
            active_order["batch_progress_pct"] = round((cum / tot * 100), 1) if tot else 0

            daily_cap = prod_targets.get(lid) or 1000
            active_order["daily_target_units"] = daily_cap
            est_days = math.ceil(rem / max(daily_cap, 1)) if rem > 0 else 0
            active_order["estimated_days_remaining"] = est_days

            try:
                cur_d = date.fromisoformat(cd if day else date.today().isoformat())
                est_finish = (cur_d + timedelta(days=est_days)).isoformat()
            except Exception:
                est_finish = active_order.get("due_date", "")
            active_order["estimated_completion_date"] = est_finish
            due = active_order.get("due_date", "9999-12-31")
            active_order["schedule_health"] = "on_schedule" if est_finish <= due else "delayed"

            active_orders[lid] = active_order
            if lid not in next_orders:
                curr_idx = sorted_orders.index(active_order) if active_order in sorted_orders else -1
                if curr_idx >= 0 and curr_idx + 1 < len(sorted_orders):
                    next_orders[lid] = sorted_orders[curr_idx + 1]
                else:
                    queued = [o for o in sorted_orders if o.get("status") == "queued" and o.get("order_id") != active_order.get("order_id")]
                    if queued:
                        next_orders[lid] = queued[0]

    return {
        "orders": filtered,
        "completed_orders": completed_orders,
        "active_orders": active_orders,
        "next_orders": next_orders,
    }


@app.post("/api/factories/{factory_id}/orders/dispatch-next")
def dispatch_next_order(factory_id: str, payload: DispatchOrderRequest) -> dict:
    folder = _factory_dir(factory_id)
    orders_path = folder / "orders.json"
    orders = _read_json(orders_path)
    if not isinstance(orders, list):
        raise HTTPException(status_code=404, detail="Orders catalog not found")

    target_line = payload.line_id.upper()
    line_orders = [o for o in orders if str(o.get("line_id", "")).upper() == target_line]
    sorted_orders = sorted(line_orders, key=lambda x: (x.get("order_number") or 999, x.get("start_date") or ""))

    target_order = None
    if payload.order_id:
        target_order = next((o for o in sorted_orders if o.get("order_id") == payload.order_id), None)
    if not target_order:
        target_order = next((o for o in sorted_orders if o.get("status") == "queued"), None)
    if not target_order:
        target_order = next((o for o in sorted_orders if o.get("status") != "in_production"), None)

    if not target_order:
        return {"success": False, "message": f"No additional orders waiting in queue for Line {target_line}"}

    for o in line_orders:
        if o.get("status") == "in_production" and o != target_order:
            o["status"] = "completed"
            if not o.get("completion_date"):
                o["completion_date"] = date.today().isoformat()

    target_order["status"] = "in_production"
    target_order["start_date"] = date.today().isoformat()

    with orders_path.open("w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2)

    return {
        "success": True,
        "message": f"Order {target_order['order_id']} ({target_order.get('product_name')}) dispatched and loaded onto Line {target_line}.",
        "dispatched_order": target_order,
    }


@app.get("/api/factories/{factory_id}/bill-of-materials")
def bill_of_materials(factory_id: str) -> list[dict]:
    return _read_csv(_factory_dir(factory_id) / "bill_of_materials.csv")


class AssignOperatorRequest(BaseModel):
    machine_id: str = Field(min_length=2, max_length=32)
    employee_id: str = Field(min_length=2, max_length=32)


class UnassignOperatorRequest(BaseModel):
    machine_id: str = Field(min_length=2, max_length=32)


@app.get("/api/factories/{factory_id}/employees")
def factory_employees(
    factory_id: str,
    line_id: str | None = Query(default=None, description="Optional line filter"),
    status: str | None = Query(default=None, description="Optional status filter"),
    skill: str | None = Query(default=None, description="Optional skill query"),
) -> dict:
    folder = _factory_dir(factory_id)
    employees = _read_json(folder / "employees.json")
    if not isinstance(employees, list):
        return {"summary": {}, "employees": [], "active_operators": [], "free_pool": [], "absent": [], "machine_coverage": {}}

    filtered = []
    for emp in employees:
        if line_id and emp.get("line_id") and str(emp.get("line_id", "")).upper() != line_id.upper():
            continue
        if status and str(emp.get("status", "")).lower() != status.lower():
            continue
        if skill:
            skill_lower = skill.lower()
            emp_skills = [s.lower() for s in emp.get("skills", [])]
            if not any(skill_lower in s for s in emp_skills) and skill_lower not in emp.get("primary_skill", "").lower():
                continue
        filtered.append(emp)

    total_roster = len(employees)
    present_count = sum(1 for e in employees if e.get("attendance") == "present")
    absent_count = sum(1 for e in employees if e.get("attendance") == "absent")
    free_pool = [e for e in employees if e.get("status") == "free_pool"]
    free_pool_count = len(free_pool)
    attendance_pct = round((present_count / total_roster * 100), 1) if total_roster else 0

    machine_coverage = {}
    primary_by_machine: dict[str, dict] = {}
    replacement_by_machine: dict[str, dict] = {}

    for e in employees:
        m_id = e.get("machine_id")
        if not m_id:
            continue
        if e.get("is_replacement"):
            replacement_by_machine[m_id] = e
        else:
            primary_by_machine[m_id] = e

    unstaffed_count = 0
    for m_id, primary in primary_by_machine.items():
        is_absent = primary.get("attendance") == "absent"
        replacement = replacement_by_machine.get(m_id)
        is_staffed = not is_absent or (replacement is not None)
        if not is_staffed:
            unstaffed_count += 1

        active_op = replacement if (is_absent and replacement) else (primary if not is_absent else None)
        req_skill = primary.get("primary_skill", "")

        eligible_floaters = [
            {
                "employee_id": fp["employee_id"],
                "name": fp["name"],
                "role": fp["role"],
                "skills": fp.get("skills", []),
                "clock_in": fp.get("clock_in", "")
            }
            for fp in free_pool
            if any(req_skill.lower() in s.lower() for s in fp.get("skills", [])) or req_skill.lower() in fp.get("primary_skill", "").lower()
        ]

        machine_coverage[m_id] = {
            "machine_id": m_id,
            "workstation_name": primary.get("workstation_name", m_id),
            "line_id": primary.get("line_id", ""),
            "primary_operator": primary,
            "is_absent": is_absent,
            "is_staffed": is_staffed,
            "active_operator": active_op,
            "is_replacement": bool(is_absent and replacement),
            "required_skill": req_skill,
            "eligible_free_pool": eligible_floaters,
        }

    return {
        "summary": {
            "total_roster": total_roster,
            "present_count": present_count,
            "absent_count": absent_count,
            "unstaffed_count": unstaffed_count,
            "active_operating_count": sum(1 for e in employees if e.get("status") == "active_operating"),
            "free_pool_count": free_pool_count,
            "attendance_pct": f"{attendance_pct}%",
            "shift": "Shift A (06:00 - 14:00)"
        },
        "employees": filtered,
        "machine_coverage": machine_coverage,
        "active_operators": [e for e in filtered if e.get("status") == "active_operating"],
        "free_pool": free_pool,
        "absent": [e for e in filtered if e.get("status") == "absent"],
    }


@app.post("/api/factories/{factory_id}/assign-operator")
def assign_operator(factory_id: str, payload: AssignOperatorRequest) -> dict:
    folder = _factory_dir(factory_id)
    emp_path = folder / "employees.json"
    if not emp_path.exists():
        raise HTTPException(status_code=404, detail="Employees catalog not found.")

    employees = json.loads(emp_path.read_text(encoding="utf-8"))

    candidate = next((e for e in employees if e.get("employee_id") == payload.employee_id), None)
    if not candidate:
        raise HTTPException(status_code=404, detail=f"Employee {payload.employee_id} not found.")

    if candidate.get("status") != "free_pool":
        raise HTTPException(status_code=400, detail=f"Employee {candidate['name']} is not in the Free Pool.")

    primary = next((e for e in employees if e.get("machine_id") == payload.machine_id and not e.get("is_replacement")), None)
    if not primary:
        raise HTTPException(status_code=404, detail=f"No primary machine mapping found for {payload.machine_id}.")

    candidate["status"] = "active_operating"
    candidate["line_id"] = primary.get("line_id")
    candidate["line_name"] = primary.get("line_name")
    candidate["machine_id"] = payload.machine_id
    candidate["workstation_name"] = primary.get("workstation_name")
    candidate["is_replacement"] = True
    candidate["replacement_for"] = primary.get("employee_id")
    primary["replacement_employee_id"] = candidate["employee_id"]

    emp_path.write_text(json.dumps(employees, indent=2, ensure_ascii=False), encoding="utf-8")

    # Immediately sync today's machine_status.json on disk
    today_str = datetime.now().astimezone().date().isoformat()
    mach_path = folder / today_str / "machine_status.json"
    if mach_path.exists():
        try:
            mach_data = json.loads(mach_path.read_text(encoding="utf-8"))
            for m in mach_data.get("machines", []):
                if m.get("machine_id") == payload.machine_id:
                    m["is_unstaffed"] = False
                    m["status"] = m.get("operational_status", "running")
            mach_path.write_text(json.dumps(mach_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # Immediately sync today's production.csv so the line starts running right away
    prod_path = folder / today_str / "production.csv"
    if prod_path.exists():
        try:
            prod_rows = _read_csv(prod_path)
            for r in prod_rows:
                if str(r.get("line_id", "")).upper() == str(primary.get("line_id", "")).upper():
                    r["line_status"] = "running"
                    curr_units = int(float(r.get("achieved_units") or 0))
                    if curr_units == 0:
                        r["achieved_units"] = "18"
                        r["attainment_pct"] = "1.9"
                        r["shortfall_units"] = str(max(0, int(r.get("target_units") or 960) - 18))
            if prod_rows:
                _write_csv(prod_path, tuple(prod_rows[0].keys()), prod_rows)
        except Exception:
            pass

    # Immediately unblock production in the simulator daemon and tick so units accumulate right away
    if _sim_instance:
        line_key = (normalize_factory_id(factory_id), primary.get("line_id"))
        _sim_instance.line_status[line_key] = "running"
        try:
            _sim_instance.tick()
        except Exception as e:
            print(f"[Simulator Tick on Assign Error] {e}")

    return {
        "success": True,
        "message": f"Assigned {candidate['name']} ({candidate['employee_id']}) to {primary.get('workstation_name')}.",
        "machine_id": payload.machine_id,
        "assigned_operator": candidate["name"],
        "covered_absent": primary["name"]
    }


@app.post("/api/factories/{factory_id}/unassign-operator")
def unassign_operator(factory_id: str, payload: UnassignOperatorRequest) -> dict:
    folder = _factory_dir(factory_id)
    emp_path = folder / "employees.json"
    if not emp_path.exists():
        raise HTTPException(status_code=404, detail="Employees catalog not found.")

    employees = json.loads(emp_path.read_text(encoding="utf-8"))

    replacement = next((e for e in employees if e.get("machine_id") == payload.machine_id and e.get("is_replacement")), None)
    primary = next((e for e in employees if e.get("machine_id") == payload.machine_id and not e.get("is_replacement")), None)

    if not replacement:
        raise HTTPException(status_code=400, detail=f"No replacement operator is assigned to {payload.machine_id}.")

    rep_name = replacement["name"]
    replacement["status"] = "free_pool"
    replacement["line_id"] = None
    replacement["line_name"] = None
    replacement["machine_id"] = None
    replacement["workstation_name"] = None
    replacement["is_replacement"] = False
    replacement["replacement_for"] = None

    if primary:
        primary["replacement_employee_id"] = None

    emp_path.write_text(json.dumps(employees, indent=2, ensure_ascii=False), encoding="utf-8")

    # Sync today's machine_status.json
    today_str = datetime.now().astimezone().date().isoformat()
    mach_path = folder / today_str / "machine_status.json"
    if mach_path.exists():
        try:
            mach_data = json.loads(mach_path.read_text(encoding="utf-8"))
            for m in mach_data.get("machines", []):
                if m.get("machine_id") == payload.machine_id:
                    m["is_unstaffed"] = True
                    m["status"] = "unstaffed"
            mach_path.write_text(json.dumps(mach_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # Sync today's production.csv
    prod_path = folder / today_str / "production.csv"
    if prod_path.exists() and primary:
        try:
            prod_rows = _read_csv(prod_path)
            for r in prod_rows:
                if str(r.get("line_id", "")).upper() == str(primary.get("line_id", "")).upper():
                    r["line_status"] = "operator_shortage"
            if prod_rows:
                _write_csv(prod_path, tuple(prod_rows[0].keys()), prod_rows)
        except Exception:
            pass

    if _sim_instance and primary:
        try:
            _sim_instance.tick()
        except Exception as e:
            print(f"[Simulator Tick on Unassign Error] {e}")

    return {
        "success": True,
        "message": f"Returned {rep_name} to Free Pool. {payload.machine_id} is now unstaffed.",
        "machine_id": payload.machine_id
    }


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
    emp_path = _factory_dir(factory_id) / "employees.json"
    if emp_path.exists():
        try:
            employees = _read_json(emp_path)
            if isinstance(employees, list):
                unstaffed_ids = {
                    e.get("machine_id") for e in employees
                    if e.get("attendance") == "absent" and not e.get("replacement_employee_id") and e.get("machine_id")
                }
                for m in snapshot.get("machines", []):
                    mid = m.get("machine_id")
                    if mid in unstaffed_ids:
                        m["is_unstaffed"] = True
                        m["status"] = "unstaffed"
                    elif m.get("is_unstaffed"):
                        m["is_unstaffed"] = False
                        if m.get("status") == "unstaffed":
                            m["status"] = m.get("operational_status", "running")
        except Exception:
            pass
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
    question: str = Field(min_length=2, max_length=1000)
    factory_id: str = Field(default="F001", min_length=1, max_length=32)
    multi_agent: bool = Field(default=False)
    history: list[dict[str, str]] | None = Field(default=None)


@app.post("/api/agent/query")
def agent_query(payload: AgentQueryRequest) -> dict:
    """Run an AI investigation via single-agent harness or multi-agent supervisor."""
    if payload.multi_agent:
        try:
            from v2.multi_agent import SupervisorAgent
        except ModuleNotFoundError:
            from multi_agent import SupervisorAgent
        supervisor = SupervisorAgent()
        result = supervisor.run(question=payload.question, factory_id=payload.factory_id)
        return result.to_dict()
    else:
        try:
            from v2.harness import AgentHarness
        except ModuleNotFoundError:
            from harness import AgentHarness
        harness = AgentHarness()
        result = harness.run(
            question=payload.question,
            factory_id=payload.factory_id,
            history=payload.history,
        )
        return result.to_dict()


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
