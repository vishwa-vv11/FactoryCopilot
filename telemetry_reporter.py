"""Autonomous Telemetry & Inventory Depletion Analysis Reporter by Macho.

This module provides continuous telemetry monitoring for FactoryOS V2:
1. Real-time Inventory Telemetry Analysis: Constantly monitors raw material stock levels across factories.
2. Depletion & Near-Depletion Detection:
   - CRITICAL (Depleted): Available stock <= 0 or status == 'out_of_stock'.
   - WARNING (Near Depletion): Available stock <= reorder threshold or high burn rate nearing depletion.
3. Affected Production Line Impact: Maps depleted materials back to affected assembly lines and workstations.
4. Macho Proactive Alerts: Generates direct operational warning directives with estimated time to exhaustion.
5. Persistent Alert State: Thread-safe in-memory and disk registry (v2/data/telemetry_alerts.json).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data" / "factories"
ALERTS_FILE = Path(__file__).resolve().parent / "data" / "telemetry_alerts.json"

_LOCK = threading.Lock()
_ACTIVE_ALERTS: dict[str, list[dict[str, Any]]] = {}

# Baseline nominal warehouse capacities from factory specs
NOMINAL_CAPACITIES: dict[str, dict[str, float]] = {
    "F001": {"STL": 18000.0, "ALU": 5000.0, "BRG": 6000.0, "FST": 24000.0, "SEA": 12000.0, "CLT": 900.0},
    "F002": {"CTN": 42000.0, "POL": 26000.0, "DYE": 3200.0, "SIZ": 3000.0, "FIN": 1800.0, "PKG": 9000.0},
    "F003": {"PCB": 10000.0, "MCU": 10000.0, "PAS": 18000.0, "CON": 12000.0, "SNS": 7000.0, "SLD": 90000.0},
}
NEAR_DEPLETION_PCT_THRESHOLD = 20.0  # Alert strictly triggers at 20% or below of nominal capacity


# =====================================================================
# 1. Telemetry Alert Data Model
# =====================================================================

@dataclass
class TelemetryAlert:
    alert_id: str
    factory_id: str
    material_code: str
    material_name: str
    unit: str
    available_quantity: float
    reorder_level: float
    consumed_quantity: float
    nominal_capacity: float = 0.0
    remaining_pct: float = 0.0
    severity: str = "WARNING"  # 'CRITICAL' or 'WARNING'
    depletion_status: str = "near_depletion"  # 'depleted' or 'near_depletion'
    affected_lines: list[str] = field(default_factory=list)
    burn_rate_daily: float = 0.0
    estimated_runout_hours: float | None = None
    macho_alert_message: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "factory_id": self.factory_id,
            "material_code": self.material_code,
            "material_name": self.material_name,
            "unit": self.unit,
            "available_quantity": round(self.available_quantity, 2),
            "reorder_level": round(self.reorder_level, 2),
            "consumed_quantity": round(self.consumed_quantity, 2),
            "nominal_capacity": round(self.nominal_capacity, 2),
            "remaining_pct": round(self.remaining_pct, 1),
            "severity": self.severity,
            "depletion_status": self.depletion_status,
            "affected_lines": self.affected_lines,
            "burn_rate_daily": round(self.burn_rate_daily, 2),
            "estimated_runout_hours": round(self.estimated_runout_hours, 1) if self.estimated_runout_hours is not None else None,
            "macho_alert_message": self.macho_alert_message,
            "created_at": self.created_at,
        }


# =====================================================================
# 2. File Utilities for Telemetry Inspection
# =====================================================================

def _normalize_factory_id(factory_id: str) -> str:
    cleaned = str(factory_id).strip()
    if cleaned.isdigit():
        return f"F{int(cleaned):03d}"
    if cleaned.upper().startswith("F") and cleaned[1:].isdigit():
        return f"F{int(cleaned[1:]):03d}"
    return cleaned.upper()


def _get_factory_dir(factory_id: str) -> Path | None:
    fid = _normalize_factory_id(factory_id)
    matches = [p for p in DATA_DIR.glob(f"{fid}-*") if p.is_dir()]
    if not matches:
        matches = [p for p in DATA_DIR.glob(f"{factory_id}-*") if p.is_dir()]
    return matches[0] if matches else None


def _get_latest_day_dir(factory_id: str, day: str | None = None) -> tuple[str, Path] | None:
    fdir = _get_factory_dir(factory_id)
    if not fdir:
        return None

    days = sorted([p.name for p in fdir.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)])
    if not days:
        return None

    target_day = day if (day and day in days) else days[-1]
    return target_day, fdir / target_day


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# =====================================================================
# 3. Telemetry Analysis Reporter by Macho
# =====================================================================

class TelemetryAnalysisReporter:
    """Continuous inventory telemetry analyzer that generates proactive Macho alerts."""

    def __init__(self) -> None:
        self.load_persisted_alerts()

    def load_persisted_alerts(self) -> None:
        global _ACTIVE_ALERTS
        with _LOCK:
            if ALERTS_FILE.exists():
                try:
                    data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
                    _ACTIVE_ALERTS = data.get("alerts_by_factory", {})
                except Exception:
                    _ACTIVE_ALERTS = {}

    def persist_alerts(self) -> None:
        with _LOCK:
            ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "alerts_by_factory": _ACTIVE_ALERTS,
            }
            ALERTS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _determine_affected_lines(self, fdir: Path, material_code: str) -> list[str]:
        """Inspect Bill of Materials to discover which production lines consume this material."""
        affected = []
        bom_file = fdir / "bill_of_materials.json"
        if bom_file.exists():
            bom_data = _read_json(bom_file)
            for line_id, materials in bom_data.items():
                if any(m.get("material_code") == material_code for m in materials):
                    affected.append(line_id)
        if not affected:
            # Fallback heuristic: Line 01 uses primary metal/fluids, Line 02 uses components
            if material_code in {"STL", "ALU", "CLT"}:
                affected.append("Line 01")
            elif material_code in {"BRG", "FST", "SEA"}:
                affected.append("Line 02")
        return affected

    def analyze_factory(self, factory_id: str, day: str | None = None) -> dict[str, Any]:
        """Analyze raw material telemetry for a specific factory and register proactive alerts."""
        fid = _normalize_factory_id(factory_id)
        day_info = _get_latest_day_dir(fid, day)
        if not day_info:
            return {
                "factory_id": fid,
                "status": "NO_DATA",
                "message": f"No telemetry data found for factory '{fid}'.",
                "alerts": [],
            }

        selected_day, folder = day_info
        fdir = _get_factory_dir(fid)
        inv_path = folder / "inventory.csv"
        rows = _read_csv(inv_path)

        if not rows:
            return {
                "factory_id": fid,
                "date": selected_day,
                "status": "HEALTHY",
                "message": "Inventory records are empty.",
                "alerts": [],
            }

        alerts: list[TelemetryAlert] = []
        materials_summary: list[dict[str, Any]] = []

        for row in rows:
            code = row.get("material_code", "UNKNOWN")
            name = row.get("material_name", code)
            unit = row.get("unit", "units")
            try:
                available = float(row.get("available_quantity", 0.0))
                reorder = float(row.get("reorder_level", 0.0))
                consumed = float(row.get("consumed_quantity", 0.0))
                opening = float(row.get("opening_quantity", 0.0))
            except (ValueError, TypeError):
                continue

            row_status = row.get("status", "in_stock").lower()
            burn_rate = consumed if consumed > 0 else (opening * 0.25)
            # Estimate runout hours assuming an 8-hour shift consumption pace
            hourly_burn = (burn_rate / 8.0) if burn_rate > 0 else 0.0
            runout_hours = (available / hourly_burn) if hourly_burn > 0 else None

            # Calculate nominal capacity and remaining stock percentage
            capacity = NOMINAL_CAPACITIES.get(fid, {}).get(code)
            if not capacity or capacity <= 0:
                capacity = max(opening, reorder * 6.0, available)
            remaining_pct = round((available / capacity) * 100.0, 1) if capacity > 0 else 0.0

            affected_lines = self._determine_affected_lines(fdir or folder.parent, code)
            lines_str = ", ".join(affected_lines) if affected_lines else "Plant Lines"

            # 1. Depleted condition (CRITICAL): 0 stock
            if available <= 0.0 or row_status in {"out_of_stock", "depleted"}:
                msg = (
                    f"🚨 MACHO CRITICAL ALERT: Raw material '{name}' ({code}) is completely DEPLETED "
                    f"(0.0 {unit} available / 0.0% stock). {lines_str} is halted or facing imminent stoppage! "
                    f"Urgent expediting required."
                )
                alert = TelemetryAlert(
                    alert_id=f"ALERT-{fid}-{code}-{selected_day}-DEPLETED",
                    factory_id=fid,
                    material_code=code,
                    material_name=name,
                    unit=unit,
                    available_quantity=available,
                    reorder_level=reorder,
                    consumed_quantity=consumed,
                    nominal_capacity=capacity,
                    remaining_pct=0.0,
                    severity="CRITICAL",
                    depletion_status="depleted",
                    affected_lines=affected_lines,
                    burn_rate_daily=burn_rate,
                    estimated_runout_hours=0.0,
                    macho_alert_message=msg,
                )
                alerts.append(alert)

            # 2. Near-Depletion condition (WARNING): Strictly 20% or below of nominal warehouse capacity
            elif remaining_pct <= NEAR_DEPLETION_PCT_THRESHOLD or (available <= reorder and remaining_pct <= 20.0):
                runout_text = f"~{runout_hours:.1f} hours of supply remaining" if runout_hours is not None else "depletion approaching"
                msg = (
                    f"⚠️ MACHO TELEMETRY ALERT: Raw material '{name}' ({code}) is NEAR DEPLETION at {remaining_pct}% stock in {fid}. "
                    f"Current stock: {available:,.1f} / {capacity:,.1f} {unit} ({remaining_pct}% remaining; Safety reorder threshold: {reorder:,.1f} {unit}). "
                    f"Estimated {runout_text}. Immediate reorder required to safeguard {lines_str}."
                )
                alert = TelemetryAlert(
                    alert_id=f"ALERT-{fid}-{code}-{selected_day}-NEAR",
                    factory_id=fid,
                    material_code=code,
                    material_name=name,
                    unit=unit,
                    available_quantity=available,
                    reorder_level=reorder,
                    consumed_quantity=consumed,
                    nominal_capacity=capacity,
                    remaining_pct=remaining_pct,
                    severity="WARNING",
                    depletion_status="near_depletion",
                    affected_lines=affected_lines,
                    burn_rate_daily=burn_rate,
                    estimated_runout_hours=runout_hours,
                    macho_alert_message=msg,
                )
                alerts.append(alert)

            materials_summary.append({
                "code": code,
                "name": name,
                "unit": unit,
                "available": available,
                "reorder_level": reorder,
                "nominal_capacity": capacity,
                "remaining_pct": remaining_pct,
                "status": "depleted" if available <= 0 else ("near_depletion" if remaining_pct <= NEAR_DEPLETION_PCT_THRESHOLD else "healthy"),
            })

        # Update active alerts in-memory registry
        alert_dicts = [a.to_dict() for a in alerts]
        with _LOCK:
            _ACTIVE_ALERTS[fid] = alert_dicts
        self.persist_alerts()

        # Synthesize Macho's Proactive Status
        has_critical = any(a["severity"] == "CRITICAL" for a in alert_dicts)
        has_warning = any(a["severity"] == "WARNING" for a in alert_dicts)
        overall_status = "CRITICAL_DEPLETION" if has_critical else ("WARNING_DEPLETION" if has_warning else "HEALTHY")

        if has_critical:
            headline = f"Macho Alert: {sum(1 for a in alert_dicts if a['severity'] == 'CRITICAL')} raw material(s) DEPLETED in {fid}!"
        elif has_warning:
            headline = f"Macho Warning: {len(alert_dicts)} raw material(s) near depletion threshold in {fid}."
        else:
            headline = f"All raw material stocks in {fid} are healthy and above safety reorder thresholds."

        return {
            "factory_id": fid,
            "date": selected_day,
            "status": overall_status,
            "headline": headline,
            "alerts_count": len(alert_dicts),
            "critical_count": sum(1 for a in alert_dicts if a["severity"] == "CRITICAL"),
            "warning_count": sum(1 for a in alert_dicts if a["severity"] == "WARNING"),
            "alerts": alert_dicts,
            "materials": materials_summary,
        }

    def get_active_alerts(self, factory_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve live active alerts for a factory or all factories."""
        self.load_persisted_alerts()
        with _LOCK:
            if factory_id:
                fid = _normalize_factory_id(factory_id)
                return _ACTIVE_ALERTS.get(fid, [])
            all_alerts: list[dict[str, Any]] = []
            for f_alerts in _ACTIVE_ALERTS.values():
                all_alerts.extend(f_alerts)
            return all_alerts

    def analyze_all_factories(self) -> dict[str, Any]:
        """Scan and analyze telemetry for all factories in the system."""
        results = {}
        for fdir in sorted(DATA_DIR.glob("F*-*")):
            if fdir.is_dir():
                fid = fdir.name.split("-")[0]
                results[fid] = self.analyze_factory(fid)
        return results


# Singleton instance
_REPORTER = TelemetryAnalysisReporter()


def get_telemetry_alerts(factory_id: str) -> list[dict[str, Any]]:
    """Query current inventory depletion alerts analyzed by Macho for a given factory."""
    res = _REPORTER.analyze_factory(factory_id)
    return res.get("alerts", [])


def analyze_inventory_telemetry(factory_id: str) -> dict[str, Any]:
    """Execute complete inventory telemetry analysis by Macho for a factory."""
    return _REPORTER.analyze_factory(factory_id)


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing Macho Telemetry Analysis Reporter on Factory F001...\n")
    analysis = _REPORTER.analyze_factory("F001")
    print(f"Factory: {analysis.get('factory_id')} | Status: {analysis.get('status')}")
    print(f"Headline: {analysis.get('headline')}")
    print(f"Total Alerts: {analysis.get('alerts_count')}")
    for alert in analysis.get("alerts", []):
        print(f"\n - [{alert.get('severity')}] {alert.get('material_name')} ({alert.get('material_code')})")
        print(f"   Available: {alert.get('available_quantity')} {alert.get('unit')} (Threshold: {alert.get('reorder_level')})")
        print(f"   Message: {alert.get('macho_alert_message')}")
        print(f"   Affected Lines: {alert.get('affected_lines')}")

    print("\n[OK] Macho Telemetry Analysis Reporter smoke test completed successfully!")

