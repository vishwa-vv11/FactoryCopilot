"""Allowlisted, read-only operational tools for FactoryOS V2.

This module provides:
1. Tool Functions: Pure Python functions that query factory data (production, downtime, inventory, etc.).
2. OpenAI-Compatible Schemas: JSON Schemas defining tool names, descriptions, and parameter constraints.
3. Evidence Engine: Converts raw operational records into traceable, indexed evidence items (e.g. E1, E2).
4. Tool Registry / Executor: Dispatches model tool calls safely with parameter sanitization.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import re
from typing import Any, Callable

from .rag import search_factory_documents

# Base directory for V2 factory data
DATA_DIR = Path(__file__).resolve().parent / "data" / "factories"
FACTORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_ROWS = 50


# =====================================================================
# File Helper Utilities (Safe, Read-Only File Access)
# =====================================================================

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
        raise ValueError(f"Invalid factory ID: {factory_id}")
    matches = [p for p in DATA_DIR.glob(f"{fid}-*") if p.is_dir()]
    if not matches:
        matches = [p for p in DATA_DIR.glob(f"{factory_id}-*") if p.is_dir()]
    if not matches:
        raise ValueError(f"Factory '{factory_id}' not found in {DATA_DIR}")
    return matches[0]


def _day_dir(factory_id: str, day: str | None = None) -> tuple[date, Path]:
    fdir = _factory_dir(factory_id)
    available = sorted([p.name for p in fdir.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)])
    if not available:
        raise ValueError(f"No operational data available for factory '{factory_id}'")

    cleaned_day = str(day).strip().lower() if day else ""
    if not cleaned_day or cleaned_day in ("today", "latest", "now", "current"):
        selected_day = date.fromisoformat(available[-1])
    elif cleaned_day in ("yesterday", "yday", "previous", "prior", "last", "prev"):
        if len(available) >= 2:
            selected_day = date.fromisoformat(available[-2])
        else:
            selected_day = date.fromisoformat(available[-1]) - timedelta(days=1)
    else:
        try:
            selected_day = date.fromisoformat(cleaned_day)
        except (ValueError, TypeError):
            selected_day = date.fromisoformat(available[-1])

    folder = fdir / selected_day.isoformat()
    if not folder.is_dir():
        selected_day = date.fromisoformat(available[-1])
        folder = fdir / selected_day.isoformat()
    return selected_day, folder



def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# =====================================================================
# 1. Operational Tool Implementations
# =====================================================================

def get_factory_overview(factory_id: str) -> dict[str, Any]:
    """Retrieve factory catalog, lines, products, and machines."""
    folder = _factory_dir(factory_id)
    return _read_json(folder / "factory.json")


def get_production_data(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve line-by-line production figures (targets, achieved units, shortfall)."""
    selected_day, folder = _day_dir(factory_id, day)
    rows = _read_csv(folder / "production.csv")
    if line_id:
        rows = [r for r in rows if r.get("line_id") == line_id]
    return rows[:limit]


def get_downtime_data(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
    machine_id: str | None = None,
    limit: int = 20,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Retrieve machine stoppage events, duration in minutes, and failure reasons."""
    fdir = _factory_dir(factory_id)
    if day:
        cd = str(day).strip().lower()
        if re.search(r"\[e\d+\]", cd) or not (re.match(r"^\d{4}-\d{2}-\d{2}$", cd) or cd in ("today", "yesterday", "yday", "previous", "prior", "last", "prev")):
            day = None

    available = sorted([p.name for p in fdir.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)], reverse=True)

    if day:
        selected_day, folder = _day_dir(factory_id, day)
        rows = _read_csv(folder / "downtime.csv")
        status_path = folder / "machine_status.json"
        as_of = None
        if status_path.exists():
            try:
                as_of = _read_json(status_path).get("as_of")
            except Exception:
                pass
        if as_of and selected_day.isoformat() == str(as_of)[:10]:
            rows = [r for r in rows if not r.get("started_at") or r.get("started_at") <= as_of]

        for r in rows:
            if not r.get("date"):
                r["date"] = selected_day.isoformat()
            r.pop("event_id", None)
        if line_id:
            rows = [r for r in rows if r.get("line_id") == line_id]
        if machine_id:
            rows = [r for r in rows if r.get("machine_id") == machine_id]
            if not rows:
                for d in available:
                    if d == selected_day.isoformat():
                        continue
                    d_rows = [r for r in _read_csv(fdir / d / "downtime.csv") if r.get("machine_id") == machine_id]
                    for r in d_rows:
                        if not r.get("date"):
                            r["date"] = d
                        r.pop("event_id", None)
                    rows.extend(d_rows)
                    if len(rows) >= limit:
                        break
        return rows[:limit]

    all_rows = []
    for d in available:
        rows = _read_csv(fdir / d / "downtime.csv")
        status_path = fdir / d / "machine_status.json"
        as_of = None
        if status_path.exists():
            try:
                as_of = _read_json(status_path).get("as_of")
            except Exception:
                pass
        if as_of and d == str(as_of)[:10]:
            rows = [r for r in rows if not r.get("started_at") or r.get("started_at") <= as_of]

        for r in rows:
            if not r.get("date"):
                r["date"] = d
            r.pop("event_id", None)
            if line_id and r.get("line_id") != line_id:
                continue
            if machine_id and r.get("machine_id") != machine_id:
                continue
            all_rows.append(r)
            if len(all_rows) >= limit:
                return all_rows
    return all_rows


def get_machine_logs(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
    machine_id: str | None = None,
    event_type: str | None = None,
    limit: int = 3,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Retrieve machine lifecycle event logs, maintenance completions, repair events, and telemetry status changes. Use event_type='repair' to check when a machine was repaired or serviced."""
    fdir = _factory_dir(factory_id)
    if day:
        cd = str(day).strip().lower()
        if re.search(r"\[e\d+\]", cd) or not (re.match(r"^\d{4}-\d{2}-\d{2}$", cd) or cd in ("today", "yesterday", "yday", "previous", "prior", "last", "prev")):
            day = None

    # Normalize machine_id if given in shorthand
    if machine_id:
        mid_clean = str(machine_id).strip().upper()
        if mid_clean in ("1", "M1", "M01"):
            machine_id = "L01-M01" if not line_id or line_id == "L01" else f"{line_id}-M01"

    req_repair = bool(event_type and any(w in str(event_type).lower() for w in ("repair", "maintenance", "service")))

    available = sorted([p.name for p in fdir.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)], reverse=True)

    def _format_log(log: dict[str, Any], date_val: str) -> dict[str, Any] | None:
        raw_r = str(log.get("reason", "")).lower()
        is_repair = ("maintenance" in raw_r or "serviced" in raw_r or (log.get("from_status") == "down" and log.get("status") == "running"))
        if req_repair and not is_repair:
            return None
        item: dict[str, Any] = {
            "date": date_val,
            "timestamp": log.get("timestamp"),
            "machine_id": log.get("machine_id"),
            "line_id": log.get("line_id"),
            "event": log.get("reason"),
            "from_status": log.get("from_status"),
            "status": log.get("status"),
        }
        if is_repair:
            item["repair_status"] = f"Repaired & serviced on {date_val} at {log.get('timestamp')}"
        return item

    if day:
        selected_day, folder = _day_dir(factory_id, day)
        raw_logs = _read_jsonl(folder / "machine_logs.jsonl")
        logs = []
        for raw in raw_logs:
            if line_id and raw.get("line_id") != line_id:
                continue
            if machine_id and raw.get("machine_id") != machine_id:
                continue
            formatted = _format_log(raw, selected_day.isoformat())
            if formatted:
                logs.append(formatted)
        if logs:
            return logs[:limit]

    all_logs = []
    repair_logs = []
    for d in available:
        raw_logs = _read_jsonl(fdir / d / "machine_logs.jsonl")
        for raw in raw_logs:
            if line_id and raw.get("line_id") != line_id:
                continue
            if machine_id and raw.get("machine_id") != machine_id:
                continue
            formatted = _format_log(raw, d)
            if formatted:
                if formatted.get("repair_status"):
                    repair_logs.append(formatted)
                else:
                    all_logs.append(formatted)

    if req_repair or repair_logs:
        return (repair_logs if repair_logs else all_logs)[:limit]
    return all_logs[:limit]



def get_inventory_data(
    factory_id: str,
    day: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve raw materials stock levels, reorder thresholds, and shortages."""
    selected_day, folder = _day_dir(factory_id, day)
    rows = _read_csv(folder / "inventory.csv")
    return rows[:limit]


def get_quality_data(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve inspection yields, defects, rework units, and rejection rates."""
    selected_day, folder = _day_dir(factory_id, day)
    rows = _read_csv(folder / "quality.csv")
    if line_id:
        rows = [r for r in rows if r.get("line_id") == line_id]
    return rows[:limit]


def get_machine_status(
    factory_id: str,
    day: str | None = None,
    machine_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve machine operational state (running, down, idle) and telemetry."""
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
    if machine_id and "machines" in snapshot:
        filtered = [m for m in snapshot["machines"] if m.get("machine_id") == machine_id]
        snapshot["machines"] = filtered
    return snapshot


def get_shortfall_analysis(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
) -> list[dict[str, Any]]:
    """Correlate production shortfall with machine downtime, unstaffed operator bottlenecks, inventory shortages, and quality defects."""
    selected_day, folder = _day_dir(factory_id, day)
    prod_rows = _read_csv(folder / "production.csv")
    downtime_rows = _read_csv(folder / "downtime.csv")
    inventory_rows = _read_csv(folder / "inventory.csv")
    quality_rows = _read_csv(folder / "quality.csv")

    status_path = folder / "machine_status.json"
    as_of = None
    if status_path.exists():
        try:
            as_of = _read_json(status_path).get("as_of")
        except Exception:
            pass
    if as_of and selected_day.isoformat() == str(as_of)[:10]:
        downtime_rows = [d for d in downtime_rows if not d.get("started_at") or d.get("started_at") <= as_of]

    emp_path = _factory_dir(factory_id) / "employees.json"
    unstaffed_by_line: dict[str, list[dict]] = {}
    if emp_path.exists():
        try:
            employees = _read_json(emp_path)
            for e in employees:
                if e.get("attendance") == "absent" and not e.get("replacement_employee_id"):
                    lid = str(e.get("line_id", "")).upper()
                    unstaffed_by_line.setdefault(lid, []).append(e)
        except Exception:
            pass

    if line_id:
        prod_rows = [r for r in prod_rows if r.get("line_id") == line_id]

    results = []
    for prod in prod_rows:
        current_line = prod.get("line_id", "")
        tgt = int(prod.get("target_units") or 1000)
        ach = int(prod.get("achieved_units") or 0)
        active_b = int(prod.get("active_batch_units") or 0)
        cur_ach = active_b if (active_b > 0 or ach > tgt) else ach
        shortfall = max(0, tgt - cur_ach)
        line_stops = [d for d in downtime_rows if d.get("line_id") == current_line]
        line_quality = [q for q in quality_rows if q.get("line_id") == current_line]

        signals = []

        # 1. Staffing Bottlenecks (Top Priority Cause)
        unstaffed_ops = unstaffed_by_line.get(current_line, [])
        if unstaffed_ops:
            unstaffed_details = [f"Workstation '{u.get('workstation_name') or u.get('machine_id')}' ({u.get('machine_id')}) is UNSTAFFED because operator {u.get('name')} ({u.get('employee_id')}) is absent with no relief floater assigned" for u in unstaffed_ops]
            signals.append(f"CRITICAL STAFFING BOTTLENECK: {'; '.join(unstaffed_details)}. Line is HALTED due to missing operator.")

        # 2. Machine Stoppages (Active historical downtime only)
        if line_stops:
            total_downtime = sum(int(d.get("duration_minutes") or 0) for d in line_stops)
            reasons = ", ".join({d.get("reason", "unknown") for d in line_stops})
            signals.append(f"Downtime: {total_downtime} mins across {len(line_stops)} stop(s) ({reasons})")

        # 3. Material Shortages
        if prod.get("line_status") == "material_shortage":
            low_stock = [i.get("material_name") for i in inventory_rows if i.get("status") in {"reorder", "out_of_stock"}]
            signals.append(f"Material shortage reported. Low stock: {', '.join(low_stock[:3])}")

        # 4. Quality Defects
        for q in line_quality:
            rework = int(q.get("rework_units") or 0)
            rejected = int(q.get("rejected_units") or 0)
            if rework or rejected:
                signals.append(f"Quality loss: {rework} rework units, {rejected} rejected units")

        results.append({
            "line_id": current_line,
            "line_name": prod.get("line_name"),
            "line_status": "halted" if unstaffed_ops else prod.get("line_status", "running"),
            "target_units": tgt,
            "achieved_units": cur_ach,
            "shortfall_units": shortfall,
            "has_unstaffed_bottleneck": bool(unstaffed_ops),
            "unstaffed_workstations": [u.get("workstation_name") or u.get("machine_id") for u in unstaffed_ops],
            "signals": signals if signals else ["No operational disruption detected"],
        })
    return results


def get_orders_data(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
    order_id: str | None = None,
    status: str | None = None,
    limit: int = MAX_ROWS,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Retrieve manufacturing work orders including multi-day batch progress, target units, and customer."""
    path = _factory_dir(factory_id) / "orders.json"
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            orders: list[dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    selected_day, folder = _day_dir(factory_id, day)
    cd_str = selected_day.isoformat()
    prod_by_line: dict[str, dict[str, Any]] = {}
    try:
        prod_path = folder / "production.csv"
        if prod_path.is_file():
            prod_rows = _read_csv(prod_path)
            prod_by_line = {str(r.get("line_id", "")).upper(): r for r in prod_rows}
    except Exception:
        pass

    for order in orders:
        s_date = order.get("start_date", "")
        c_date = order.get("completion_date") or ""
        due_date = order.get("due_date", "9999-12-31")

        # Dynamically determine order status based on selected operational date
        if c_date and c_date < cd_str and order.get("units_completed", 0) >= order.get("total_order_units", 0):
            order["status"] = "completed"
        elif str(order.get("status", "")).lower() == "in_production":
            order["status"] = "in_production"
        elif s_date and s_date <= cd_str <= (c_date or due_date) and order.get("status") != "completed":
            order["status"] = "in_production"
        elif s_date and s_date > cd_str:
            order["status"] = "queued"

        olid = str(order.get("line_id", "")).upper()
        p_row = prod_by_line.get(olid, {})
        shift_achieved = int(float(p_row.get("achieved_units") or 0)) if p_row else 0
        shift_target = int(float(p_row.get("target_units") or 1000)) if p_row else 1000

        order["daily_target_units"] = shift_target
        order["batch_target"] = shift_target
        if order.get("status") == "in_production":
            order["units_completed_current_shift"] = shift_achieved
            order["shift_attainment_pct"] = round((shift_achieved / shift_target * 100), 1) if shift_target else 0

        # Multi-day contract metrics
        total = int(order.get("total_order_units") or 0)
        cum_completed = int(order.get("units_completed") or 0)
        if order.get("status") == "in_production" and shift_achieved > 0:
            cum_completed = max(cum_completed, shift_achieved)
        order["cumulative_units_completed"] = cum_completed
        order["units_completed"] = cum_completed

        remaining = max(0, total - cum_completed)
        order["remaining_order_units"] = remaining
        order["batch_progress_pct"] = round((cum_completed / total * 100), 1) if total else 0

        # Schedule and estimated days remaining calculation
        daily_cap = max(shift_target, 1)
        est_days = math.ceil(remaining / daily_cap) if remaining > 0 else 0
        order["estimated_days_remaining"] = est_days
        est_finish_date = (selected_day + timedelta(days=est_days)).isoformat()
        order["estimated_completion_date"] = est_finish_date

        if order.get("status") == "completed":
            order["schedule_health"] = "completed"
        elif order.get("status") == "queued":
            order["schedule_health"] = "queued"
        elif est_finish_date <= due_date:
            order["schedule_health"] = "on_schedule"
        else:
            order["schedule_health"] = "delayed"

        order["is_active_on_selected_day"] = (order.get("status") == "in_production")

    filtered = []
    for order in orders:
        if line_id and str(order.get("line_id", "")).upper() != line_id.upper():
            continue
        if order_id:
            req_oid = str(order_id).strip().upper()
            item_oid = str(order.get("order_id", "")).strip().upper()
            digits_req = re.sub(r"\D", "", req_oid)
            digits_item = re.sub(r"\D", "", item_oid)
            matches_digits = bool(digits_req and digits_item and int(digits_req) == int(digits_item))
            if req_oid != item_oid and not matches_digits:
                continue
        if status and str(order.get("status", "")).lower() != status.lower():
            continue
        filtered.append(order)
        if len(filtered) >= limit:
            break
    return filtered


def get_employees_data(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
    machine_id: str | None = None,
    employee_id: str | None = None,
    status: str | None = None,
    skill: str | None = None,
    attendance: str | None = None,
    role: str | None = None,
    limit: int = 35,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Retrieve plant workforce roster, shift attendance, machine operator assignments, cross-skilling, and free pool standby floaters."""
    path = _factory_dir(factory_id) / "employees.json"
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            employees: list[dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    emp_by_id = {e.get("employee_id"): e for e in employees}

    # Specific employee query
    if employee_id:
        match = next((e for e in employees if str(e.get("employee_id", "")).upper() == employee_id.upper()), None)
        if match:
            res = dict(match)
            rep_id = res.get("replacement_employee_id")
            if rep_id and rep_id in emp_by_id:
                res["replacement_employee_name"] = emp_by_id[rep_id].get("name")
                res["is_replaced"] = True
            return [res]
        return []

    # Specific machine query
    if machine_id:
        matches = [e for e in employees if str(e.get("machine_id", "")).upper() == machine_id.upper()]
        enriched = []
        for e in matches:
            d = dict(e)
            rep_id = d.get("replacement_employee_id")
            if rep_id and rep_id in emp_by_id:
                d["replacement_employee_name"] = emp_by_id[rep_id].get("name")
                d["is_replaced"] = True
            enriched.append(d)
        return enriched

    # Specific line query
    if line_id:
        line_matches = []
        for e in employees:
            if e.get("line_id") and str(e.get("line_id", "")).upper() == line_id.upper():
                rep_id = e.get("replacement_employee_id")
                rep_emp = emp_by_id.get(rep_id) if rep_id else None
                line_matches.append({
                    "employee_id": e.get("employee_id"),
                    "name": e.get("name"),
                    "role": e.get("role"),
                    "workstation": e.get("workstation_name"),
                    "machine_id": e.get("machine_id"),
                    "status": e.get("status"),
                    "attendance": e.get("attendance"),
                    "is_replaced": bool(rep_emp),
                    "replacement_employee_id": rep_id,
                    "replacement_employee_name": rep_emp.get("name") if rep_emp else None,
                    "coverage_status": f"Replaced by {rep_emp.get('name')} ({rep_id})" if rep_emp else ("Unstaffed" if e.get("attendance") == "absent" else "Operating")
                })
        return line_matches[:limit]

    # Free pool floaters query
    if status and status.lower() == "free_pool":
        free_pool = [
            {
                "employee_id": e.get("employee_id"),
                "name": e.get("name"),
                "role": e.get("role"),
                "primary_skill": e.get("primary_skill"),
                "skills": e.get("skills", []),
                "status": "free_pool",
                "availability": "Available on Standby in Free Pool (NOT assigned to any machine, ready for assignment)",
            }
            for e in employees
            if e.get("status") == "free_pool" and not e.get("is_replacement") and not e.get("machine_id")
        ]
        deployed_floaters = [
            {
                "employee_id": e.get("employee_id"),
                "name": e.get("name"),
                "role": e.get("role"),
                "line_id": e.get("line_id"),
                "line_name": e.get("line_name"),
                "machine_id": e.get("machine_id"),
                "workstation_name": e.get("workstation_name"),
                "status": "active_operating",
                "is_replacement": True,
                "replacement_for": e.get("replacement_for"),
                "covering_absent_operator": emp_by_id.get(e.get("replacement_for"), {}).get("name") if e.get("replacement_for") in emp_by_id else e.get("replacement_for"),
                "availability": f"Actively deployed on {e.get('workstation_name')} ({e.get('machine_id')}) covering absent {emp_by_id.get(e.get('replacement_for'), {}).get('name', 'operator')}",
            }
            for e in employees
            if e.get("is_replacement") or (e.get("replacement_for") and e.get("machine_id"))
        ]
        return (free_pool + deployed_floaters)[:limit]

    # Absenteeism query
    if (attendance and attendance.lower() == "absent") or (status and status.lower() == "absent"):
        absent = []
        for e in employees:
            if e.get("attendance") == "absent":
                rep_id = e.get("replacement_employee_id")
                rep_emp = emp_by_id.get(rep_id) if rep_id else None
                absent.append({
                    "employee_id": e.get("employee_id"),
                    "name": e.get("name"),
                    "line_id": e.get("line_id"),
                    "line_name": e.get("line_name"),
                    "machine_id": e.get("machine_id"),
                    "workstation": e.get("workstation_name"),
                    "is_replaced": bool(rep_emp),
                    "replacement_employee_id": rep_id,
                    "replacement_employee_name": rep_emp.get("name") if rep_emp else None,
                    "replacement_role": rep_emp.get("role") if rep_emp else None,
                    "coverage_status": f"Replaced by {rep_emp.get('name')} ({rep_id})" if rep_emp else "Unstaffed (No replacement assigned)",
                    "coverage_summary": f"Covered by relief floater {rep_emp.get('name')} ({rep_id})" if rep_emp else "Unstaffed bottleneck (no floater assigned)",
                    "status": "replaced" if rep_emp else "unstaffed",
                })
        return absent[:limit]

    # Broad query: return structured plant-wide workforce snapshot including replacements
    absent_list = []
    for e in employees:
        if e.get("attendance") == "absent":
            rep_id = e.get("replacement_employee_id")
            rep_emp = emp_by_id.get(rep_id) if rep_id else None
            absent_list.append({
                "employee_id": e.get("employee_id"),
                "name": e.get("name"),
                "line_id": e.get("line_id"),
                "line_name": e.get("line_name"),
                "machine_id": e.get("machine_id"),
                "workstation": e.get("workstation_name"),
                "is_replaced": bool(rep_emp),
                "replacement_employee_id": rep_id,
                "replacement_employee_name": rep_emp.get("name") if rep_emp else None,
                "coverage_status": f"Covered by relief floater {rep_emp.get('name')} ({rep_id})" if rep_emp else "Unstaffed bottleneck",
            })

    active_replacements = [
        {
            "absent_operator": a["name"],
            "absent_employee_id": a["employee_id"],
            "workstation": a["workstation"],
            "machine_id": a["machine_id"],
            "line_id": a["line_id"],
            "replacement_floater": a["replacement_employee_name"],
            "replacement_employee_id": a["replacement_employee_id"],
        }
        for a in absent_list if a["is_replaced"]
    ]

    deployed_floaters = [
        {
            "floater_name": e.get("name"),
            "floater_id": e.get("employee_id"),
            "role": e.get("role"),
            "line_id": e.get("line_id"),
            "line_name": e.get("line_name"),
            "machine_id": e.get("machine_id"),
            "workstation": e.get("workstation_name"),
            "covering_for_id": e.get("replacement_for"),
            "covering_for_name": emp_by_id.get(e.get("replacement_for"), {}).get("name") if e.get("replacement_for") in emp_by_id else None,
            "status": "active_deployed",
            "coverage_note": f"Deployed on {e.get('workstation_name')} covering absent {emp_by_id.get(e.get('replacement_for'), {}).get('name', 'operator')}"
        }
        for e in employees
        if e.get("is_replacement") or (e.get("replacement_for") and e.get("machine_id"))
    ]

    # Truly available standby floaters ONLY (unassigned, not deployed to any workstation)
    available_free_pool = [
        {
            "employee_id": e.get("employee_id"),
            "name": e.get("name"),
            "role": e.get("role"),
            "primary_skill": e.get("primary_skill"),
            "skills": e.get("skills", []),
            "status": "free_pool_available",
            "notes": "Available in standby pool for immediate dispatch"
        }
        for e in employees
        if e.get("status") == "free_pool" and not e.get("is_replacement") and not e.get("machine_id")
    ]

    lines_summary: dict[str, list[str]] = {}
    for emp in employees:
        if emp.get("machine_id") and emp.get("attendance") == "present":
            l_name = emp.get("line_name") or emp.get("line_id") or "Production Floor"
            rep = f" *(Relief for {emp.get('replacement_for')})*" if emp.get("is_replacement") else ""
            lines_summary.setdefault(l_name, []).append(
                f"{emp.get('name')} ({emp.get('employee_id')}, {emp.get('workstation_name', emp.get('machine_id'))}{rep})"
            )

    return [{
        "total_absent_count": len(absent_list),
        "absent_operators": absent_list,
        "active_replacements": active_replacements,
        "deployed_operating_floaters": deployed_floaters,
        "unstaffed_bottlenecks": [a for a in absent_list if not a["is_replaced"]],
        "available_free_pool_standby": available_free_pool,
        "present_operators_by_line": lines_summary,
    }]


def assign_floater(
    factory_id: str,
    machine_id: str,
    employee_id: str | None = None,
) -> dict[str, Any]:
    """Assign an eligible cross-trained floater from the Free Pool to an unstaffed machine workstation on explicit user command.
    Requires machine_id. Automatic unprompted allocation is strictly disabled.
    """
    if not machine_id or str(machine_id).strip().lower() in ("auto", "all", "none", ""):
        return {
            "success": False,
            "message": "Automatic plant-wide floater allocation is disabled. Please specify the target machine_id (e.g. L01-M03) on your direct command.",
            "assignments": []
        }

    folder = _factory_dir(factory_id)
    emp_path = folder / "employees.json"
    if not emp_path.exists():
        return {"success": False, "message": "Employees catalog not found.", "assignments": []}

    employees = json.loads(emp_path.read_text(encoding="utf-8"))
    free_pool = [e for e in employees if e.get("status") == "free_pool"]

    assignments = []

    # Specific assignment mode on explicit command
    target_m = str(machine_id).strip()
    primary = next((e for e in employees if str(e.get("machine_id", "")).upper() == target_m.upper() and not e.get("is_replacement")), None)
    if not primary:
        return {"success": False, "message": f"No workstation mapping found for {machine_id}.", "assignments": []}

    cand = None
    if employee_id:
        cand = next((e for e in free_pool if str(e.get("employee_id", "")).upper() == str(employee_id).upper()), None)
        if not cand:
            return {"success": False, "message": f"Employee {employee_id} is not in the Free Pool.", "assignments": []}
    else:
        req_skill = primary.get("primary_skill", "").lower()
        for fp in free_pool:
            skills = [s.lower() for s in fp.get("skills", [])]
            if req_skill in skills or req_skill in fp.get("primary_skill", "").lower():
                cand = fp
                break
        if not cand and free_pool:
            cand = free_pool[0]

    if not cand:
        return {"success": False, "message": "No available standby floaters in the Free Pool.", "assignments": []}

    cand["status"] = "active_operating"
    cand["line_id"] = primary.get("line_id")
    cand["line_name"] = primary.get("line_name")
    cand["machine_id"] = primary.get("machine_id")
    cand["workstation_name"] = primary.get("workstation_name")
    cand["is_replacement"] = True
    cand["replacement_for"] = primary.get("employee_id")
    primary["replacement_employee_id"] = cand["employee_id"]
    assignments.append({
        "machine_id": primary.get("machine_id"),
        "workstation_name": primary.get("workstation_name"),
        "line_id": primary.get("line_id"),
        "line_name": primary.get("line_name"),
        "floater_name": cand["name"],
        "floater_id": cand["employee_id"],
        "covering_for": primary["name"],
        "matched_skill": primary.get("primary_skill", "Operation"),
    })

    if assignments:
        emp_path.write_text(json.dumps(employees, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            today_str = datetime.now().astimezone().date().isoformat()
            day_folder = folder / today_str
            m_stat_path = day_folder / "machine_status.json"
            if m_stat_path.exists():
                m_data = json.loads(m_stat_path.read_text(encoding="utf-8"))
                assigned_m_ids = {a["machine_id"] for a in assignments}
                for m in m_data.get("machines", []):
                    if m.get("machine_id") in assigned_m_ids:
                        if m.get("status") == "unstaffed":
                            m["status"] = "running"
                        m["is_unstaffed"] = False
                m_stat_path.write_text(json.dumps(m_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    return {
        "success": bool(assignments),
        "assigned_count": len(assignments),
        "assignments": assignments,
        "message": f"Successfully assigned {len(assignments)} floater(s) to unstaffed workstation(s)." if assignments else "No unstaffed workstations found or no matching floaters available."
    }



def get_cross_factory_overview(day: str | None = None) -> list[dict[str, Any]]:
    """Retrieve operational performance comparison across all factories (F001, F002, F003)."""
    factories = []
    for p in sorted(DATA_DIR.glob("F*-*")):
        if not p.is_dir():
            continue
        fid = p.name.split("-")[0]
        meta = _read_json(p / "factory.json")
        try:
            selected_day, day_folder = _day_dir(fid, day)
        except Exception:
            continue
        prod_rows = _read_csv(day_folder / "production.csv")
        dt_rows = _read_csv(day_folder / "downtime.csv")

        target = sum(int(r.get("target_units") or 0) for r in prod_rows)
        achieved = sum(int(r.get("achieved_units") or 0) for r in prod_rows)
        pct = round((achieved / target * 100), 1) if target else 0
        total_dt = sum(int(d.get("duration_minutes") or 0) for d in dt_rows)

        factories.append({
            "factory_id": fid,
            "name": meta.get("name", fid),
            "industry": meta.get("industry", "Manufacturing"),
            "location": meta.get("location", ""),
            "active_lines": len(meta.get("lines", [])),
            "date": selected_day.isoformat(),
            "target_units": target,
            "achieved_units": achieved,
            "shortfall_units": target - achieved,
            "attainment_pct": pct,
            "total_downtime_minutes": total_dt,
            "status": "target_met" if pct >= 95 else ("running" if pct >= 70 else "attention_required"),
        })
    return factories


# =====================================================================
# 2. OpenAI-Compatible JSON Tool Schemas
# =====================================================================

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_orders_data",
            "description": "Retrieve multi-day manufacturing work orders, customer accounts, batch targets, units completed, and active order status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "line_id": {"type": "string", "description": "Optional line identifier (e.g. L01)"},
                    "order_id": {"type": "string", "description": "Optional work order identifier (e.g. WO-CAC-4401)"},
                    "status": {"type": "string", "description": "Optional order status (in_production, completed, queued)"},
                    "limit": {"type": "integer", "description": "Max orders to return (default 20)", "minimum": 1, "maximum": MAX_ROWS},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_employees_data",
            "description": "Retrieve factory workforce, active machine operators, shift attendance (present/absent), standby free pool floaters, and operator assignments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "attendance": {"type": "string", "description": "Filter by attendance: 'present' or 'absent'"},
                    "role": {"type": "string", "description": "Filter by role, e.g. 'operator' or 'floater'"},
                    "status": {"type": "string", "description": "Filter by status: 'active_operating', 'free_pool', 'absent'"},
                    "machine_id": {"type": "string", "description": "Filter by assigned machine ID (e.g. L01-M01)"},
                    "line_id": {"type": "string", "description": "Filter by line ID (e.g. L01)"},
                    "limit": {"type": "integer", "description": "Max rows to return (default 35)", "minimum": 1, "maximum": 50},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_factory_overview",
            "description": "Get general factory information including lines, products manufactured, and machines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001, F002, F003)"},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_production_data",
            "description": "Retrieve daily production figures including planned targets, achieved units, and shortfall.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Production date in YYYY-MM-DD format"},
                    "line_id": {"type": "string", "description": "Optional line identifier (e.g. L01)"},
                    "limit": {"type": "integer", "description": "Max rows to return (default 20)", "minimum": 1, "maximum": MAX_ROWS},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_downtime_data",
            "description": "Retrieve machine breakdown events, stoppage durations (in minutes), and logged failure reasons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "line_id": {"type": "string", "description": "Optional line identifier"},
                    "machine_id": {"type": "string", "description": "Optional machine identifier"},
                    "limit": {"type": "integer", "description": "Max rows to return", "minimum": 1, "maximum": MAX_ROWS},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory_data",
            "description": "Inspect raw material warehouse levels, safety reorder thresholds, and shortage statuses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "limit": {"type": "integer", "description": "Max rows to return", "minimum": 1, "maximum": MAX_ROWS},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_quality_data",
            "description": "Inspect inspection pass rates, defect counts, rework units, and overall yield percentages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "line_id": {"type": "string", "description": "Optional line identifier"},
                    "limit": {"type": "integer", "description": "Max rows to return", "minimum": 1, "maximum": MAX_ROWS},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_machine_status",
            "description": "Check current operational status of machines (Running, Down, Idle) and telemetry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "machine_id": {"type": "string", "description": "Optional machine identifier"},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shortfall_analysis",
            "description": "Correlate missed production targets with machine downtime, inventory shortages, and quality defects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "line_id": {"type": "string", "description": "Optional line identifier"},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_factory_documents",
            "description": "Search factory operating manuals, maintenance procedures, and safety SOPs for relevant instructions and troubleshooting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "query": {"type": "string", "description": "Technical search phrase (e.g. coolant pump vibration)"},
                    "limit": {"type": "integer", "description": "Max snippets to retrieve", "minimum": 1, "maximum": 5},
                },
                "required": ["factory_id", "query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_employees_data",
            "description": "Retrieve factory workforce roster, attendance, machine operator assignments, cross-trained skills, absent operators, and standby free pool replacements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Date in YYYY-MM-DD format, or 'today', or 'yesterday'"},
                    "line_id": {"type": "string", "description": "Optional line identifier (e.g. L01)"},
                    "machine_id": {"type": "string", "description": "Optional machine identifier (e.g. L01-M01)"},
                    "status": {"type": "string", "description": "Optional status filter ('active_operating', 'free_pool', 'absent')"},
                    "skill": {"type": "string", "description": "Optional skill query (e.g. CNC Turning, Gear Hobbing)"},
                    "attendance": {"type": "string", "description": "Optional attendance filter ('present', 'absent')"},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_floater",
            "description": "Deploy and assign an eligible standby floater from the Free Pool to an unstaffed machine workstation on explicit user command. Requires a specific target machine_id (e.g. L01-M03). Automatic allocation is disabled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "machine_id": {"type": "string", "description": "Target workstation identifier (e.g. L01-M02, L01-M03). Required on explicit user command."},
                    "employee_id": {"type": "string", "description": "Optional specific floater employee ID (e.g. EMP-131)"},
                },
                "required": ["factory_id", "machine_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_machine_logs",
            "description": "Retrieve machine lifecycle event logs, maintenance completions, repair events, servicing, and telemetry status changes across historical days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_id": {"type": "string", "description": "Factory identifier (e.g. F001)"},
                    "day": {"type": "string", "description": "Optional date in YYYY-MM-DD format (omitted to search newest to oldest across history)"},
                    "line_id": {"type": "string", "description": "Optional line identifier (e.g. L01)"},
                    "machine_id": {"type": "string", "description": "Optional machine identifier (e.g. L01-M01)"},
                    "limit": {"type": "integer", "description": "Max logs to return", "default": 20},
                },
                "required": ["factory_id"],
                "additionalProperties": False,
            },
        },
    },
]


# =====================================================================
# 3. Evidence Engine (Anti-Hallucination Citation Support)
# =====================================================================

def format_evidence_detail(tool_name: str, item: dict[str, Any]) -> str:
    """Transform raw structured records into human-readable citation snippets."""
    if tool_name == "get_orders_data":
        return f"Order '{item.get('order_id')}': {item.get('product_name')} for {item.get('customer')} (Started: {item.get('start_date')}, Due: {item.get('due_date')}, Completed: {item.get('units_completed')}/{item.get('total_order_units')} units, Status: {item.get('status')})."
    if tool_name == "get_employees_data":
        if "active_replacements" in item:
            rep_parts = [f"{r['replacement_floater']} ({r['replacement_employee_id']}) covering for {r['absent_operator']} at {r['workstation']}" for r in item.get("active_replacements", [])]
            rep_str = "; ".join(rep_parts) if rep_parts else "No relief floaters assigned"
            abs_parts = [f"{a['name']} ({a['workstation']})" for a in item.get("absent_operators", [])]
            free_parts = [f"{fp['name']} ({fp['employee_id']})" for fp in item.get("available_free_pool_standby", [])]
            free_str = ", ".join(free_parts) if free_parts else "None (All floaters assigned)"
            dep_parts = [f"{d['floater_name']} ({d['floater_id']}) deployed on {d['workstation']}" for d in item.get("deployed_operating_floaters", [])]
            dep_str = "; ".join(dep_parts) if dep_parts else "None"
            return f"Workforce snapshot: {item.get('total_absent_count', 0)} absent operator(s) [{', '.join(abs_parts)}]. Active replacements: {rep_str}. Deployed floaters: {dep_str}. Available Free Pool Standby: {free_str}."
        if "coverage_summary" in item or "is_replaced" in item:
            rep_info = f", Replaced by relief floater: {item.get('replacement_employee_name')} ({item.get('replacement_employee_id')})" if item.get("is_replaced") else ", Unstaffed (No replacement assigned)"
            return f"Absent operator '{item.get('employee_id')}' ({item.get('name')}) at {item.get('workstation') or item.get('machine_id')}{rep_info}."
        if "line_name" in item:
            ops = item.get("present_operators", [])
            ops_preview = ", ".join(ops[:6])
            if len(ops) > 6:
                ops_preview += f" (+{len(ops) - 6} more)"
            return f"Line '{item.get('line_name')}': {item.get('active_operator_count', len(ops))} active operators present on duty ({ops_preview})."
        status_info = f"Status: {item.get('status')}"
        if item.get("status") == "free_pool":
            status_info = "Status: Available on Standby in Free Pool (NOT assigned to any machine, ready for dispatch)"
        elif item.get("is_replacement"):
            covering_name = item.get("covering_absent_operator") or item.get("covering_for_name") or item.get("replacement_for") or "absent operator"
            status_info = f"Status: Actively Deployed Floater (Covering absent operator {covering_name})"
        elif item.get("is_replaced"):
            status_info = f"Status: Absent (Covered by relief floater {item.get('replacement_employee_name', item.get('replacement_employee_id'))})"
        loc = f" on {item.get('workstation_name')} ({item.get('machine_id')})" if item.get("workstation_name") else (" in Free Pool (Standby)" if item.get("status") == "free_pool" else "")
        skills = ", ".join(item.get("skills", []))
        return f"Employee '{item.get('employee_id')}' ({item.get('name')}, {item.get('role')}){loc}: {status_info}, Shift: {item.get('shift', 'Shift A')}, Skills: [{skills}]."
    if tool_name == "search_factory_documents":
        snippet = item.get("content", "").replace("\n", " ").strip()
        return f"Document '{item.get('source')}': {snippet[:200]}..."
    if tool_name == "get_production_data":
        return f"{item.get('date')}: Line {item.get('line_id')} produced {item.get('achieved_units')}/{item.get('target_units')} units (Shortfall: {item.get('shortfall_units')}, Status: {item.get('line_status')})."
    if tool_name == "get_downtime_data":
        return f"{item.get('date')}: Machine {item.get('machine_id')} on Line {item.get('line_id')} was down for {item.get('duration_minutes')} mins ({item.get('reason')})."
    if tool_name == "get_inventory_data":
        return f"Material '{item.get('material_name')}': {item.get('available_quantity')} {item.get('unit')} available (Reorder level: {item.get('reorder_level')}, Status: {item.get('status')})."
    if tool_name == "get_quality_data":
        return f"Line {item.get('line_id')}: Inspected {item.get('inspected_units')} units, Yield {item.get('yield_pct')}%, Rework: {item.get('rework_units')}, Rejected: {item.get('rejected_units')}."
    if tool_name == "assign_floater":
        ass = item.get("assignments", [])
        if not ass:
            return item.get("message", "Floater assignment executed.")
        lines = [f"{a['floater_name']} ({a['floater_id']}) assigned to {a['workstation_name']} ({a['machine_id']}) covering for {a['covering_for']}" for a in ass]
        return f"Floater assignment completed: {'; '.join(lines)}."
    if tool_name == "get_machine_logs":
        date_str = item.get("date") or str(item.get("timestamp", ""))[:10]
        raw_reason = item.get("reason") or item.get("description") or item.get("status") or "state change"
        if "maintenance" in str(raw_reason).lower() or "service" in str(raw_reason).lower() or item.get("from_status") == "down":
            action_desc = f"repaired and serviced ({raw_reason})"
        else:
            action_desc = raw_reason
        return f"{date_str}: Machine {item.get('machine_id')} ({item.get('machine_name', '')}) on Line {item.get('line_id')}: {action_desc} (Operational Status: {item.get('status')}, Timestamp: {item.get('timestamp')})."
    if tool_name == "get_shortfall_analysis":
        status_info = f"Status: {item.get('line_status', 'running')}"
        return f"Line {item.get('line_id')} ({item.get('line_name')}): {status_info}, Shortfall of {item.get('shortfall_units')} units (Target: {item.get('target_units')}, Achieved: {item.get('achieved_units')}). Observed signals: {'; '.join(item.get('signals', []))}."
    return json.dumps(item, ensure_ascii=False)


class EvidenceStore:
    """Accumulates and indexes evidence collected during an agent investigation."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def register(self, tool_name: str, result: Any) -> list[str]:
        """Convert tool result items into indexed evidence entries (E1, E2, ...)."""
        raw_items = result if isinstance(result, list) else [result]
        evidence_ids = []
        for item in raw_items:
            if not isinstance(item, dict) or not item:
                continue
            evidence_id = f"E{len(self.items) + 1}"
            entry = {
                "evidence_id": evidence_id,
                "tool": tool_name,
                "detail": format_evidence_detail(tool_name, item),
                "data": item,
            }
            self.items.append(entry)
            evidence_ids.append(evidence_id)
        return evidence_ids

    def get_all(self) -> list[dict[str, Any]]:
        return self.items

    def summary(self) -> str:
        return "\n".join(f"[{e['evidence_id']}] {e['detail']}" for e in self.items)


# =====================================================================
# 4. Tool Registry & Safe Dispatcher
# =====================================================================

class ToolRegistry:
    """Safe, allowlisted execution controller for agent tool calls."""

    _HANDLERS: dict[str, Callable] = {
        "get_factory_overview": get_factory_overview,
        "get_orders_data": get_orders_data,
        "get_employees_data": get_employees_data,
        "get_production_data": get_production_data,
        "get_downtime_data": get_downtime_data,
        "get_inventory_data": get_inventory_data,
        "get_quality_data": get_quality_data,
        "get_machine_status": get_machine_status,
        "get_shortfall_analysis": get_shortfall_analysis,
        "get_cross_factory_overview": get_cross_factory_overview,
        "search_factory_documents": search_factory_documents,
        "assign_floater": assign_floater,
        "get_machine_logs": get_machine_logs,
    }

    def __init__(self, evidence_store: EvidenceStore | None = None, allowed_tools: set[str] | None = None) -> None:
        self.evidence_store = evidence_store or EvidenceStore()
        self.allowed_tools = allowed_tools

    @property
    def schemas(self) -> list[dict[str, Any]]:
        if self.allowed_tools is not None:
            return [s for s in TOOL_SCHEMAS if s["function"]["name"] in self.allowed_tools]
        return TOOL_SCHEMAS

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate, execute the tool, and register its findings in the EvidenceStore."""
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return {
                "tool": tool_name,
                "arguments": arguments,
                "evidence_ids": [],
                "result": [],
                "error": f"Tool '{tool_name}' is not permitted for this specialist.",
            }
        handler = self._HANDLERS.get(tool_name)
        if not handler:
            raise ValueError(f"Tool '{tool_name}' is not in the allowlist.")

        # Parameter validation: factory_id pattern check
        factory_id = arguments.get("factory_id")
        if factory_id and not FACTORY_ID_PATTERN.fullmatch(factory_id):
            raise ValueError(f"Invalid factory identifier: '{factory_id}'")

        # Execute safe Python function with signature parameter filtering
        import inspect
        sig = inspect.signature(handler)
        valid_params = sig.parameters
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in valid_params.values())
        filtered_args = arguments if has_kwargs else {k: v for k, v in arguments.items() if k in valid_params}
        raw_output = handler(**filtered_args)

        # Register findings into the Evidence Store
        evidence_ids = self.evidence_store.register(tool_name, raw_output)

        return {
            "tool": tool_name,
            "arguments": arguments,
            "evidence_ids": evidence_ids,
            "result": raw_output,
        }


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing FactoryOS V2 Tool Layer & Evidence Engine...\n")
    store = EvidenceStore()
    registry = ToolRegistry(store)

    # Test 1: Production Data
    print("--- 1. Testing get_production_data ---")
    res1 = registry.execute("get_production_data", {"factory_id": "F001", "limit": 2})
    print(f"Returned {len(res1['result'])} record(s), assigned evidence: {res1['evidence_ids']}")

    # Test 2: Shortfall Analysis
    print("\n--- 2. Testing get_shortfall_analysis ---")
    res2 = registry.execute("get_shortfall_analysis", {"factory_id": "F001"})
    print(f"Analyzed {len(res2['result'])} line(s), assigned evidence: {res2['evidence_ids']}")

    # Print Evidence Summary
    print("\n--- Accumulated Evidence Store ---")
    print(store.summary())
    print("\n[OK] Tool layer smoke test completed successfully!")
