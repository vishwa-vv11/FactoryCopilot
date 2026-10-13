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
    selected_day = date.fromisoformat(day) if day else datetime.now().astimezone().date()
    folder = _factory_dir(factory_id) / selected_day.isoformat()
    if not folder.is_dir():
        # Fall back to latest available day if selected date does not exist
        available = sorted([p.name for p in _factory_dir(factory_id).iterdir() if p.is_dir()])
        if not available:
            raise ValueError(f"No operational data available for factory '{factory_id}'")
        selected_day = date.fromisoformat(available[-1])
        folder = _factory_dir(factory_id) / selected_day.isoformat()
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
) -> list[dict[str, Any]]:
    """Retrieve machine stoppage events, duration in minutes, and failure reasons."""
    selected_day, folder = _day_dir(factory_id, day)
    rows = _read_csv(folder / "downtime.csv")
    if line_id:
        rows = [r for r in rows if r.get("line_id") == line_id]
    if machine_id:
        rows = [r for r in rows if r.get("machine_id") == machine_id]
    return rows[:limit]


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
    if machine_id and "machines" in snapshot:
        filtered = [m for m in snapshot["machines"] if m.get("machine_id") == machine_id]
        snapshot["machines"] = filtered
    return snapshot


def get_shortfall_analysis(
    factory_id: str,
    day: str | None = None,
    line_id: str | None = None,
) -> list[dict[str, Any]]:
    """Correlate production shortfall with machine downtime, inventory shortages, and quality defects."""
    selected_day, folder = _day_dir(factory_id, day)
    prod_rows = _read_csv(folder / "production.csv")
    downtime_rows = _read_csv(folder / "downtime.csv")
    inventory_rows = _read_csv(folder / "inventory.csv")
    quality_rows = _read_csv(folder / "quality.csv")

    if line_id:
        prod_rows = [r for r in prod_rows if r.get("line_id") == line_id]

    results = []
    for prod in prod_rows:
        current_line = prod.get("line_id", "")
        shortfall = int(prod.get("shortfall_units") or 0)
        line_stops = [d for d in downtime_rows if d.get("line_id") == current_line]
        line_quality = [q for q in quality_rows if q.get("line_id") == current_line]

        signals = []
        if line_stops:
            total_downtime = sum(int(d.get("duration_minutes") or 0) for d in line_stops)
            reasons = ", ".join({d.get("reason", "unknown") for d in line_stops})
            signals.append(f"Downtime: {total_downtime} mins across {len(line_stops)} stop(s) ({reasons})")

        if prod.get("line_status") == "material_shortage":
            low_stock = [i.get("material_name") for i in inventory_rows if i.get("status") in {"reorder", "out_of_stock"}]
            signals.append(f"Material shortage reported. Low stock: {', '.join(low_stock[:3])}")

        for q in line_quality:
            rework = int(q.get("rework_units") or 0)
            rejected = int(q.get("rejected_units") or 0)
            if rework or rejected:
                signals.append(f"Quality loss: {rework} rework units, {rejected} rejected units")

        results.append({
            "line_id": current_line,
            "line_name": prod.get("line_name"),
            "target_units": prod.get("target_units"),
            "achieved_units": prod.get("achieved_units"),
            "shortfall_units": shortfall,
            "signals": signals if signals else ["No operational disruption detected"],
        })
    return results


# =====================================================================
# 2. OpenAI-Compatible JSON Tool Schemas
# =====================================================================

TOOL_SCHEMAS: list[dict[str, Any]] = [
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
]


# =====================================================================
# 3. Evidence Engine (Anti-Hallucination Citation Support)
# =====================================================================

def format_evidence_detail(tool_name: str, item: dict[str, Any]) -> str:
    """Transform raw structured records into human-readable citation snippets."""
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
    if tool_name == "get_shortfall_analysis":
        return f"Line {item.get('line_id')} ({item.get('line_name')}): Shortfall of {item.get('shortfall_units')} units. Observed signals: {'; '.join(item.get('signals', []))}."
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
        "get_production_data": get_production_data,
        "get_downtime_data": get_downtime_data,
        "get_inventory_data": get_inventory_data,
        "get_quality_data": get_quality_data,
        "get_machine_status": get_machine_status,
        "get_shortfall_analysis": get_shortfall_analysis,
        "search_factory_documents": search_factory_documents,
    }

    def __init__(self, evidence_store: EvidenceStore | None = None) -> None:
        self.evidence_store = evidence_store or EvidenceStore()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate, execute the tool, and register its findings in the EvidenceStore."""
        handler = self._HANDLERS.get(tool_name)
        if not handler:
            raise ValueError(f"Tool '{tool_name}' is not in the allowlist.")

        # Parameter validation: factory_id pattern check
        factory_id = arguments.get("factory_id")
        if factory_id and not FACTORY_ID_PATTERN.fullmatch(factory_id):
            raise ValueError(f"Invalid factory identifier: '{factory_id}'")

        # Execute safe Python function
        raw_output = handler(**arguments)

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
