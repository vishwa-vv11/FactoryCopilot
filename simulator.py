"""Local, file-backed simulator for three demo factories.

It writes factory/line/product catalogs, bills of materials, daily production,
machine status and event logs, downtime, and daily inventory/consumption.

Run one simulator tick (plus historical daily summaries)::

    python v2/simulator.py

Keep generating simulated activity until stopped with Ctrl+C::

    python v2/simulator.py --live --tick-seconds 10

Each tick represents one simulated production minute. Data is written beneath
``v2/data/factories/<factory-id>-<industry>/<YYYY-MM-DD>/``.
Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import random
import signal
import time as time_module
import uuid


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data" / "factories"
UTC = timezone.utc


@dataclass(frozen=True)
class MachineSpec:
    machine_id: str
    code: str
    name: str
    machine_type: str


@dataclass(frozen=True)
class MaterialSpec:
    code: str
    name: str
    unit: str
    opening_stock: float
    reorder_level: float


@dataclass(frozen=True)
class MaterialRequirement:
    material_code: str
    quantity_per_unit: float


@dataclass(frozen=True)
class LineSpec:
    line_id: str
    code: str
    name: str
    product_code: str
    product_name: str
    process_description: str
    daily_target: int
    machines: tuple[MachineSpec, ...]
    bill_of_materials: tuple[MaterialRequirement, ...]


@dataclass(frozen=True)
class FactorySpec:
    factory_id: str
    code: str
    name: str
    industry: str
    location: str
    lines: tuple[LineSpec, ...]
    materials: tuple[MaterialSpec, ...]
    failure_reason: str


def _initials(label: str) -> str:
    return "".join(word[0].upper() for word in label.split() if word)


def _line(number: int, name: str, product_name: str, target: int,
          machine_types: tuple[str, str, str],
          bill_of_materials: tuple[tuple[str, float], ...]) -> LineSpec:
    machines = tuple(
        MachineSpec(
            machine_id=f"L{number:02d}-M{index:02d}",
            code=f"{_initials(machine_type)}{index}",
            name=f"{machine_type} {index}",
            machine_type=machine_type,
        )
        for index, machine_type in enumerate(machine_types, start=1)
    )
    return LineSpec(
        line_id=f"L{number:02d}", code=_initials(name), name=name,
        product_code=_initials(product_name), product_name=product_name,
        process_description=(
            f"Produces {product_name.lower()} through "
            f"{' → '.join(machine_types)}."
        ),
        daily_target=target, machines=machines,
        bill_of_materials=tuple(MaterialRequirement(code, qty) for code, qty in bill_of_materials),
    )


FACTORIES: tuple[FactorySpec, ...] = (
    FactorySpec(
        factory_id="F001", code="CAC", name="Coimbatore Auto Components", industry="automotive",
        location="Coimbatore",
        materials=(
            MaterialSpec("STL", "Steel blanks", "pieces", 18000, 3000),
            MaterialSpec("ALU", "Aluminum alloy", "kg", 5000, 800),
            MaterialSpec("BRG", "Bearings", "pieces", 6000, 1000),
            MaterialSpec("FST", "Fasteners", "pieces", 24000, 4000),
            MaterialSpec("SEA", "Caliper seals", "pieces", 12000, 2000),
            MaterialSpec("CLT", "Coolant", "liters", 900, 150),
        ),
        lines=(
            _line(1, "Engine Components", "Finished Crankshaft", 960,
                  ("CNC Turning", "Milling", "Gun Drilling", "Induction Hardening",
                   "Cylindrical Grinding", "Ultrasonic Cleaning", "Automated Deburring",
                   "Dynamic Balancing", "Laser CMM", "Final Inspection"),
                  (("STL", 1.0), ("CLT", 0.05), ("FST", 1.0))),
            _line(2, "Transmission Gears", "Finished Transmission Gear", 900,
                  ("Forging Prep", "Gear Hobbing", "Gear Shaping", "Carburizing Furnace",
                   "Quenching Bath", "Shot Peening", "Gear Grinding",
                   "Tooth Profile CMM", "Washing Station", "Final Pack"),
                  (("STL", 0.8), ("BRG", 1.0), ("CLT", 0.03))),
            _line(3, "Brake Assemblies", "Finished Brake Caliper", 840,
                  ("Sand Casting Prep", "Shot Blasting", "CNC Milling", "Bore Honing",
                   "Leak Pressure Test", "Seal Assembly", "Piston Insertion",
                   "Torque Verification", "Laser Marking", "Final Inspection"),
                  (("ALU", 1.1), ("SEA", 2.0), ("FST", 4.0))),
        ),
        failure_reason="tool wear or coolant fault",
    ),
    FactorySpec(
        factory_id="F002", code="MTW", name="Mysuru Textile Works", industry="textiles",
        location="Mysuru",
        materials=(
            MaterialSpec("CTN", "Cotton fiber", "kg", 42000, 7000),
            MaterialSpec("POL", "Polyester fiber", "kg", 26000, 4500),
            MaterialSpec("DYE", "Textile dye", "kg", 3200, 550),
            MaterialSpec("SIZ", "Sizing starch", "kg", 3000, 500),
            MaterialSpec("FIN", "Finishing agent", "liters", 1800, 300),
            MaterialSpec("PKG", "Fabric roll packaging", "roll kits", 9000, 1500),
        ),
        lines=(
            _line(1, "Cotton Fabric", "Packed Cotton Fabric Roll", 1100,
                  ("Bale Opening", "Carding Machine", "Drawing Frame", "Roving Frame",
                   "Ring Spinning", "Winding Machine", "Warping Mill",
                   "Weaving Loom", "Dyeing Jet", "Stenter & Inspection"),
                  (("CTN", 4.0), ("DYE", 0.15), ("SIZ", 0.1), ("FIN", 0.08), ("PKG", 1.0))),
            _line(2, "Denim Fabric", "Packed Denim Fabric Roll", 1000,
                  ("Yarn Prep", "Rope Dyeing", "Sizing Range", "Air Jet Loom",
                   "Desizing Wash", "Mercerization", "Sanforizing",
                   "Inspection Table", "Rolling Unit", "Packaging Station"),
                  (("CTN", 2.0), ("POL", 2.0), ("DYE", 0.2), ("FIN", 0.1), ("PKG", 1.0))),
            _line(3, "Blended Fabric", "Packed Blended Fabric Roll", 900,
                  ("Fiber Blending", "Carding Web", "Drafting System", "Vortex Spinning",
                   "Assembly Winder", "Twisting Machine", "Rapier Loom",
                   "Heat Setting", "Color Fastness QA", "Roll Packaging"),
                  (("CTN", 2.0), ("POL", 3.0), ("FIN", 0.12), ("PKG", 1.0))),
        ),
        failure_reason="thread break or loom stoppage",
    ),
    FactorySpec(
        factory_id="F003", code="HEA", name="Hosur Electronics Assembly", industry="electronics",
        location="Hosur",
        materials=(
            MaterialSpec("PCB", "Printed circuit boards", "pieces", 10000, 1700),
            MaterialSpec("MCU", "Microcontrollers", "pieces", 10000, 1700),
            MaterialSpec("PAS", "Resistor and capacitor sets", "sets", 18000, 3000),
            MaterialSpec("CON", "Connector sets", "sets", 12000, 2000),
            MaterialSpec("SNS", "Sensors", "pieces", 7000, 1200),
            MaterialSpec("SLD", "Solder wire", "grams", 90000, 15000),
        ),
        lines=(
            _line(1, "Control Boards", "Tested Control Board", 1200,
                  ("Solder Paste Printer", "Pick & Place High-Speed", "Pick & Place Multi",
                   "Reflow Oven", "AOI Optical Inspection", "Through-Hole Insertion",
                   "Wave Soldering", "In-Circuit Tester", "Conformal Coating", "Final Pack"),
                  (("PCB", 1.0), ("MCU", 1.0), ("PAS", 1.0), ("CON", 1.0), ("SLD", 5.0))),
            _line(2, "Sensor Modules", "Tested Sensor Module", 1050,
                  ("SMT Stencil Printer", "Chip Mounter High-Speed", "Component Mounter Fine",
                   "Nitrogen Reflow Oven", "3D AOI Inspection", "Selective Soldering",
                   "Sensor Integration", "Thermal Calibration", "X-Ray Inspection", "Final Pack"),
                  (("PCB", 1.0), ("MCU", 1.0), ("SNS", 2.0), ("CON", 1.0), ("SLD", 5.0))),
            _line(3, "Power Modules", "Tested Power Module", 960,
                  ("Substrate Feeder", "High-Speed SMT Placer", "Odd-Form Inserter",
                   "Vacuum Reflow", "Automated X-Ray (AXI)", "Heatsink Attachment",
                   "Torque Screwdriving", "Burn-in Testing", "Hipot Safety Test", "Final Pack"),
                  (("PCB", 1.0), ("MCU", 2.0), ("PAS", 2.0), ("CON", 2.0), ("SLD", 8.0))),
        ),
        failure_reason="feeder fault or test-station calibration",
    ),
)

PRODUCTION_FIELDS = (
    "date", "factory_id", "factory_name", "industry", "line_id", "line_code",
    "line_name", "product_code", "product_name", "target_units", "achieved_units",
    "shortfall_units", "attainment_pct", "line_status",
)
DOWNTIME_FIELDS = (
    "event_id", "factory_id", "line_id", "machine_id", "started_at",
    "ended_at", "duration_minutes", "reason",
)
INVENTORY_FIELDS = (
    "date", "factory_id", "material_code", "material_name", "unit",
    "opening_quantity", "received_quantity", "consumed_quantity",
    "available_quantity", "reorder_level", "status",
)
DELIVERY_FIELDS = (
    "event_id", "factory_id", "material_code", "material_name", "supplier_name",
    "expected_at", "actual_at", "quantity", "unit", "status", "delay_minutes",
)
BOM_FIELDS = (
    "factory_id", "line_id", "product_code", "product_name",
    "material_code", "material_name", "unit", "quantity_per_finished_unit",
)
QUALITY_FIELDS = (
    "date", "factory_id", "line_id", "line_name", "product_name",
    "inspected_units", "passed_units", "rework_units", "rejected_units",
    "yield_pct",
)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


class FactorySimulator:
    """Generate repeatable historical summaries and persistent live events."""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR, seed: int = 20261009):
        self.data_dir = data_dir
        self.seed = seed
        self.random = random.Random(seed)
        self.achieved: dict[tuple[str, str], int] = {}
        self.rate_remainder: dict[tuple[str, str], int] = {}
        self.line_status: dict[tuple[str, str], str] = {}
        self.stock: dict[tuple[str, str], float] = {}
        self.daily_opening_stock: dict[tuple[str, str], float] = {}
        self.daily_consumed: dict[tuple[str, str], float] = {}
        self.daily_received: dict[tuple[str, str], float] = {}
        self.quality_rework: dict[tuple[str, str], int] = {}
        self.quality_rejected: dict[tuple[str, str], int] = {}
        self.delivered_today: set[tuple[str, str, date]] = set()
        self.status: dict[tuple[str, str], str] = {}
        self.down_ticks: dict[tuple[str, str], int] = {}
        self.open_downtime: dict[tuple[str, str], dict] = {}
        self.tick_number = 0
        self.stop_requested = False
        self.simulated_now: datetime | None = None
        self.active_day: date | None = None

        for factory in FACTORIES:
            for material in factory.materials:
                key = (factory.factory_id, material.code)
                self.stock[key] = material.opening_stock
                self.daily_opening_stock[key] = material.opening_stock
                self.daily_consumed[key] = 0.0
                self.daily_received[key] = 0.0
            for line in factory.lines:
                key = (factory.factory_id, line.line_id)
                self.achieved[key] = 0
                self.rate_remainder[key] = 0
                self.line_status[key] = "running"
                self.quality_rework[key] = 0
                self.quality_rejected[key] = 0
                for machine in line.machines:
                    machine_key = (factory.factory_id, machine.machine_id)
                    self.status[machine_key] = "running"
                    self.down_ticks[machine_key] = 0

    def factory_dir(self, factory: FactorySpec) -> Path:
        return self.data_dir / f"{factory.factory_id}-{factory.industry}"

    def day_dir(self, factory: FactorySpec, day: date) -> Path:
        return self.factory_dir(factory) / day.isoformat()

    @staticmethod
    def _supplier_name(factory: FactorySpec, material: MaterialSpec) -> str:
        return f"{factory.code} {material.name} Supplier"

    @staticmethod
    def _delivery_quantity(material: MaterialSpec) -> float:
        return round(max(material.reorder_level * 2, material.opening_stock * 0.2), 3)

    def write_factory_metadata(self) -> None:
        """Write a catalog for the UI, including all line and machine labels."""
        for factory in FACTORIES:
            path = self.factory_dir(factory) / "factory.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "factory_id": factory.factory_id,
                "factory_code": factory.code,
                "name": factory.name,
                "industry": factory.industry,
                "location": factory.location,
                "lines": [
                    {
                        "line_id": line.line_id,
                        "code": line.code,
                        "name": line.name,
                        "daily_target": line.daily_target,
                        "product_code": line.product_code,
                        "product_name": line.product_name,
                        "process_description": line.process_description,
                        "finished_output_stage": line.machines[-1].machine_type,
                        "machines": [machine.__dict__ for machine in line.machines],
                    }
                    for line in factory.lines
                ],
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            (self.factory_dir(factory) / "materials.json").write_text(
                json.dumps([
                    {
                        **material.__dict__,
                        "supplier_name": self._supplier_name(factory, material),
                        "delivery_every_days": 3,
                        "delivery_quantity": self._delivery_quantity(material),
                    }
                    for material in factory.materials
                ], indent=2),
                encoding="utf-8",
            )
            material_by_code = {material.code: material for material in factory.materials}
            bom_rows = []
            for line in factory.lines:
                for requirement in line.bill_of_materials:
                    material = material_by_code[requirement.material_code]
                    bom_rows.append({
                        "factory_id": factory.factory_id,
                        "line_id": line.line_id,
                        "product_code": line.product_code,
                        "product_name": line.product_name,
                        "material_code": material.code,
                        "material_name": material.name,
                        "unit": material.unit,
                        "quantity_per_finished_unit": requirement.quantity_per_unit,
                    })
            _write_csv(self.factory_dir(factory) / "bill_of_materials.csv", BOM_FIELDS, bom_rows)

    def generate_history(self, days: int, today: date | None = None) -> None:
        """Create rich operational history including downtime, machine breakdowns, inventory, and quality defects."""
        if days < 0:
            raise ValueError("history days cannot be negative")
        today = today or datetime.now().date()
        for factory in FACTORIES:
            for offset in range(days, -1, -1):
                day = today - timedelta(days=offset)
                day_folder = self.day_dir(factory, day)
                day_folder.mkdir(parents=True, exist_ok=True)
                stamp_prefix = day.isoformat()

                rows = []
                downtime_records = []
                machine_log_records = []
                quality_events = []
                machines_status_list = []

                is_today = (offset == 0)

                for line_index, line in enumerate(factory.lines):
                    rng = random.Random(f"{self.seed}:{factory.factory_id}:{line.line_id}:{day.isoformat()}")
                    
                    # Force a realistic breakdown on line 0 for today and select historical days
                    has_breakdown = (line_index == 0 and (is_today or (day.day % 2 == 1)))
                    disruption = rng.uniform(0.60, 0.78) if has_breakdown else rng.uniform(0.95, 1.0)
                    
                    target = line.daily_target
                    achieved = round(target * disruption)
                    if is_today and has_breakdown:
                        achieved = round(target * 0.65)  # Currently lagging behind target
                    
                    status_label = "machine_down" if (is_today and has_breakdown) else (
                        "target_met" if achieved >= target else "material_shortage" if not has_breakdown else "machine_down"
                    )
                    rows.append(self._production_row(factory, line, day, achieved, line_status=status_label))

                    # Machine states & downtime events
                    for m_idx, machine in enumerate(line.machines):
                        m_down = (has_breakdown and m_idx == 0)
                        curr_status = "down" if (is_today and m_down) else "running"
                        fail_reason = factory.failure_reason if m_down else ""
                        
                        machines_status_list.append({
                            "machine_id": machine.machine_id,
                            "code": machine.code,
                            "name": machine.name,
                            "line_id": line.line_id,
                            "line_name": line.name,
                            "status": curr_status,
                            "reason": fail_reason,
                        })

                        if m_down:
                            downtime_mins = rng.randint(45, 110)
                            dt_record = {
                                "event_id": f"DT-{uuid.uuid4().hex[:8]}",
                                "factory_id": factory.factory_id,
                                "line_id": line.line_id,
                                "machine_id": machine.machine_id,
                                "started_at": f"{stamp_prefix}T09:30:00+00:00",
                                "ended_at": "" if (is_today and curr_status == "down") else f"{stamp_prefix}T11:15:00+00:00",
                                "duration_minutes": 0 if (is_today and curr_status == "down") else downtime_mins,
                                "reason": factory.failure_reason,
                            }
                            downtime_records.append(dt_record)

                            machine_log_records.append({
                                "event_id": uuid.uuid4().hex,
                                "timestamp": f"{stamp_prefix}T09:30:00+00:00",
                                "factory_id": factory.factory_id,
                                "factory_name": factory.name,
                                "industry": factory.industry,
                                "line_id": line.line_id,
                                "line_code": line.code,
                                "line_name": line.name,
                                "machine_id": machine.machine_id,
                                "machine_code": machine.code,
                                "machine_name": machine.name,
                                "machine_type": machine.machine_type,
                                "from_status": "running",
                                "status": "down",
                                "reason": factory.failure_reason,
                            })

                self._write_day_summary(factory, day, rows)
                _write_csv(day_folder / "downtime.csv", DOWNTIME_FIELDS, downtime_records)

                # Machine status snapshot & logs
                status_payload = {
                    "as_of": f"{stamp_prefix}T14:00:00+00:00",
                    "factory_id": factory.factory_id,
                    "factory_name": factory.name,
                    "machines": machines_status_list,
                }
                (day_folder / "machine_status.json").write_text(json.dumps(status_payload, indent=2), encoding="utf-8")
                
                log_lines = [json.dumps(r) for r in machine_log_records]
                if log_lines:
                    (day_folder / "machine_logs.jsonl").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

                # Quality data & defects
                quality_rows = []
                for line_index, line in enumerate(factory.lines):
                    production = rows[line_index]
                    rate = 0.045 if (line_index == 0 and has_breakdown) else 0.012
                    defects = round(production["achieved_units"] * rate)
                    rework = round(defects * 0.55)
                    quality_rows.append(self._quality_row(factory, line, day, production["achieved_units"], rework, defects - rework))
                    if defects > 0:
                        quality_events.append({
                            "event_id": uuid.uuid4().hex,
                            "timestamp": f"{stamp_prefix}T10:45:00Z",
                            "factory_id": factory.factory_id,
                            "line_id": line.line_id,
                            "line_name": line.name,
                            "product_name": line.product_name,
                            "inspected_units": production["achieved_units"],
                            "rework_units": rework,
                            "rejected_units": defects - rework,
                            "reason": "surface dimensional variance" if factory.industry == "automotive" else "tolerance variance",
                        })
                _write_csv(day_folder / "quality.csv", QUALITY_FIELDS, quality_rows)
                if quality_events:
                    (day_folder / "quality_events.jsonl").write_text("\n".join([json.dumps(q) for q in quality_events]) + "\n", encoding="utf-8")

                # Inventory snapshot & deliveries
                inv_rows = []
                for material in factory.materials:
                    consumed = round(material.opening_stock * 0.35, 1)
                    available = round(material.opening_stock - consumed, 1)
                    if is_today and material.code == factory.materials[0].code:
                        available = round(material.reorder_level * 0.8, 1)  # Trigger reorder alert!
                    inv_rows.append({
                        "date": day.isoformat(),
                        "factory_id": factory.factory_id,
                        "material_code": material.code,
                        "material_name": material.name,
                        "unit": material.unit,
                        "opening_quantity": material.opening_stock,
                        "received_quantity": 0.0,
                        "consumed_quantity": consumed,
                        "available_quantity": available,
                        "reorder_level": material.reorder_level,
                        "status": "reorder_required" if available <= material.reorder_level else "healthy",
                    })
                _write_csv(day_folder / "inventory.csv", INVENTORY_FIELDS, inv_rows)

                # Scheduled delivery
                delivery_event = {
                    "event_id": f"DEL-{factory.factory_id}-{factory.materials[0].code}-{day:%Y%m%d}",
                    "factory_id": factory.factory_id,
                    "material_code": factory.materials[0].code,
                    "material_name": factory.materials[0].name,
                    "supplier_name": self._supplier_name(factory, factory.materials[0]),
                    "expected_at": f"{stamp_prefix}T09:00:00Z",
                    "actual_at": f"{stamp_prefix}T11:30:00Z",
                    "quantity": self._delivery_quantity(factory.materials[0]),
                    "unit": factory.materials[0].unit,
                    "status": "delivered_late" if (offset % 2 == 1) else "delivered_on_time",
                    "delay_minutes": 150 if (offset % 2 == 1) else 0,
                }
                (day_folder / "deliveries.jsonl").write_text(json.dumps(delivery_event) + "\n", encoding="utf-8")

    def _production_row(self, factory: FactorySpec, line: LineSpec,
                        day: date, achieved: int, line_status: str | None = None) -> dict:
        return {
            "date": day.isoformat(),
            "factory_id": factory.factory_id,
            "factory_name": factory.name,
            "industry": factory.industry,
            "line_id": line.line_id,
            "line_code": line.code,
            "line_name": line.name,
            "product_code": line.product_code,
            "product_name": line.product_name,
            "target_units": line.daily_target,
            "achieved_units": achieved,
            "shortfall_units": max(0, line.daily_target - achieved),
            "attainment_pct": round(achieved / line.daily_target * 100, 1),
            "line_status": line_status or ("target_met" if achieved >= line.daily_target else "historical"),
        }

    def _write_day_summary(self, factory: FactorySpec, day: date,
                           rows: list[dict]) -> None:
        folder = self.day_dir(factory, day)
        _write_csv(folder / "production.csv", PRODUCTION_FIELDS, rows)

    def _quality_row(self, factory: FactorySpec, line: LineSpec, day: date,
                     inspected: int, rework: int, rejected: int) -> dict:
        passed = max(0, inspected - rework - rejected)
        return {
            "date": day.isoformat(), "factory_id": factory.factory_id,
            "line_id": line.line_id, "line_name": line.name,
            "product_name": line.product_name, "inspected_units": inspected,
            "passed_units": passed, "rework_units": rework,
            "rejected_units": rejected,
            "yield_pct": round(passed / inspected * 100, 2) if inspected else 0.0,
        }

    def _record_quality(self, factory: FactorySpec, line: LineSpec, day: date,
                        timestamp: str, completed: int) -> None:
        """Apply a small per-unit defect chance and persist the resulting batch."""
        if completed <= 0:
            return
        defects = sum(1 for _ in range(completed) if self.random.random() < 0.018)
        if not defects:
            return
        rework = sum(1 for _ in range(defects) if self.random.random() < 0.6)
        rejected = defects - rework
        key = (factory.factory_id, line.line_id)
        self.quality_rework[key] += rework
        self.quality_rejected[key] += rejected
        reasons = {
            "automotive": ("dimensional variance", "surface finish defect", "assembly torque out of range"),
            "textiles": ("weaving defect", "shade variation", "fabric measurement out of tolerance"),
            "electronics": ("solder joint defect", "component placement variance", "test failure"),
        }
        _append_jsonl(self.day_dir(factory, day) / "quality_events.jsonl", {
            "event_id": uuid.uuid4().hex, "timestamp": timestamp,
            "factory_id": factory.factory_id, "line_id": line.line_id,
            "line_name": line.name, "product_name": line.product_name,
            "inspected_units": completed, "rework_units": rework,
            "rejected_units": rejected, "reason": self.random.choice(reasons[factory.industry]),
        })

    def _read_today_totals(self, day: date) -> None:
        """Resume today's achieved totals if the simulator was restarted."""
        for factory in FACTORIES:
            folder = self.day_dir(factory, day)
            self._restore_inventory(factory, day)
            path = folder / "production.csv"
            inventory_path = folder / "inventory.csv"
            if path.exists():
                with path.open(newline="", encoding="utf-8") as stream:
                    for row in csv.DictReader(stream):
                        key = (factory.factory_id, row["line_id"])
                        if key in self.achieved:
                            self.achieved[key] = int(row["achieved_units"])
            quality_path = folder / "quality.csv"
            if quality_path.exists():
                with quality_path.open(newline="", encoding="utf-8") as stream:
                    for row in csv.DictReader(stream):
                        key = (factory.factory_id, row["line_id"])
                        if key in self.quality_rework:
                            self.quality_rework[key] = int(row["rework_units"])
                            self.quality_rejected[key] = int(row["rejected_units"])
            # Migrate an already-generated V2 day that predates inventory
            # tracking by accounting for its finished units once.
            if path.exists() and not inventory_path.exists():
                for line in factory.lines:
                    completed = self.achieved[(factory.factory_id, line.line_id)]
                    if completed:
                        self._consume_materials(factory, line, completed)
            status_path = folder / "machine_status.json"
            if status_path.exists():
                status_snapshot = json.loads(status_path.read_text(encoding="utf-8"))
                raw_time_str = status_snapshot["as_of"].replace("Z", "+00:00")
                saved_time = datetime.fromisoformat(raw_time_str)
                if self.simulated_now is None or saved_time > self.simulated_now:
                    self.simulated_now = saved_time

            # Recover each machine's last status and any downtime that was
            # still open when the previous simulator process stopped.
            log_path = folder / "machine_logs.jsonl"
            if not log_path.exists():
                latest = {}
            else:
                latest: dict[str, dict] = {}
                for raw_line in log_path.read_text(encoding="utf-8").splitlines():
                    if raw_line.strip():
                        record = json.loads(raw_line)
                        latest[record["machine_id"]] = record
            for machine_id, record in latest.items():
                machine_key = (factory.factory_id, machine_id)
                if machine_key not in self.status:
                    continue
                self.status[machine_key] = record["status"]
                if record["status"] == "down":
                    self.open_downtime[machine_key] = {
                        "event_id": record["event_id"],
                        "factory_id": factory.factory_id,
                        "line_id": record["line_id"],
                        "machine_id": machine_id,
                        "started_at": record["timestamp"],
                        "ended_at": "",
                        "duration_minutes": 0,
                        "reason": record["reason"],
                    }
            deliveries_path = folder / "deliveries.jsonl"
            if deliveries_path.exists():
                for raw_line in deliveries_path.read_text(encoding="utf-8").splitlines():
                    if raw_line.strip():
                        record = json.loads(raw_line)
                        self.delivered_today.add((factory.factory_id, record["material_code"], day))

    def _restore_inventory(self, factory: FactorySpec, today: date) -> None:
        """Resume stock from today's or the most recent earlier daily snapshot."""
        candidates = []
        for path in self.factory_dir(factory).glob("*/inventory.csv"):
            try:
                snapshot_day = date.fromisoformat(path.parent.name)
            except ValueError:
                continue
            if snapshot_day <= today:
                candidates.append((snapshot_day, path))
        if not candidates:
            return
        snapshot_day, path = max(candidates, key=lambda item: item[0])
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                key = (factory.factory_id, row["material_code"])
                if key not in self.stock:
                    continue
                self.stock[key] = float(row["available_quantity"])
                if snapshot_day == today:
                    self.daily_opening_stock[key] = float(row["opening_quantity"])
                    self.daily_consumed[key] = float(row["consumed_quantity"])
                    self.daily_received[key] = float(row["received_quantity"])
                else:
                    self.daily_opening_stock[key] = self.stock[key]
                    self.daily_consumed[key] = 0.0
                    self.daily_received[key] = 0.0

    def _process_deliveries(self, factory: FactorySpec, now: datetime) -> None:
        """Apply scheduled receipts once, recording expected and actual arrival."""
        day = now.date()
        for index, material in enumerate(factory.materials):
            receipt_key = (factory.factory_id, material.code, day)
            # Stagger six materials across a repeating three-day schedule.
            if (day.toordinal() + index) % 3 != 0 or receipt_key in self.delivered_today:
                continue
            expected = datetime.combine(day, time(hour=9), tzinfo=now.tzinfo)
            delay_rng = random.Random(f"{self.seed}:{factory.factory_id}:{material.code}:{day.isoformat()}")
            delay = timedelta(hours=2) if delay_rng.random() < 0.15 else timedelta(0)
            actual = expected + delay
            if now < actual:
                continue
            quantity = self._delivery_quantity(material)
            key = (factory.factory_id, material.code)
            event = {
                "event_id": f"DEL-{factory.factory_id}-{material.code}-{day:%Y%m%d}",
                "factory_id": factory.factory_id,
                "material_code": material.code,
                "material_name": material.name,
                "supplier_name": self._supplier_name(factory, material),
                "expected_at": expected.isoformat(timespec="minutes"),
                "actual_at": actual.isoformat(timespec="minutes"),
                "quantity": quantity,
                "unit": material.unit,
                "status": "delivered_late" if delay else "delivered_on_time",
                "delay_minutes": int(delay.total_seconds() // 60),
            }
            self.stock[key] = round(self.stock[key] + quantity, 3)
            self.daily_received[key] = round(self.daily_received[key] + quantity, 3)
            _append_jsonl(self.day_dir(factory, day) / "deliveries.jsonl", event)
            self.delivered_today.add(receipt_key)

    def _write_inventory(self, factory: FactorySpec, day: date) -> None:
        rows = []
        for material in factory.materials:
            key = (factory.factory_id, material.code)
            available = round(self.stock[key], 3)
            rows.append({
                "date": day.isoformat(),
                "factory_id": factory.factory_id,
                "material_code": material.code,
                "material_name": material.name,
                "unit": material.unit,
                "opening_quantity": round(self.daily_opening_stock[key], 3),
                "received_quantity": round(self.daily_received[key], 3),
                "consumed_quantity": round(self.daily_consumed[key], 3),
                "available_quantity": available,
                "reorder_level": material.reorder_level,
                "status": "out_of_stock" if available <= 0 else (
                    "reorder" if available <= material.reorder_level else "in_stock"
                ),
            })
        _write_csv(self.day_dir(factory, day) / "inventory.csv", INVENTORY_FIELDS, rows)

    def _available_units(self, factory: FactorySpec, line: LineSpec) -> int:
        limits = []
        for requirement in line.bill_of_materials:
            key = (factory.factory_id, requirement.material_code)
            limits.append(int((self.stock[key] + 1e-9) / requirement.quantity_per_unit))
        return min(limits, default=0)

    def _consume_materials(self, factory: FactorySpec, line: LineSpec, units: int) -> None:
        for requirement in line.bill_of_materials:
            key = (factory.factory_id, requirement.material_code)
            quantity = requirement.quantity_per_unit * units
            self.stock[key] = round(max(0.0, self.stock[key] - quantity), 3)
            self.daily_consumed[key] = round(self.daily_consumed[key] + quantity, 3)

    def _write_current_status(self, factory: FactorySpec, day: date,
                              timestamp: str) -> None:
        folder = self.day_dir(factory, day)
        folder.mkdir(parents=True, exist_ok=True)
        machine_rows = []
        production_rows = []
        for line in factory.lines:
            achieved = self.achieved[(factory.factory_id, line.line_id)]
            key = (factory.factory_id, line.line_id)
            production_rows.append(self._production_row(factory, line, day, achieved, self.line_status[key]))
            for machine in line.machines:
                machine_rows.append({
                    "machine_id": machine.machine_id,
                    "machine_code": machine.code,
                    "name": machine.name,
                    "machine_type": machine.machine_type,
                    "factory_id": factory.factory_id,
                    "line_id": line.line_id,
                    "status": self.status[(factory.factory_id, machine.machine_id)],
                    "updated_at": timestamp,
                })
        _write_csv(folder / "production.csv", PRODUCTION_FIELDS, production_rows)
        quality_rows = [
            self._quality_row(
                factory, line, day, self.achieved[(factory.factory_id, line.line_id)],
                self.quality_rework[(factory.factory_id, line.line_id)],
                self.quality_rejected[(factory.factory_id, line.line_id)],
            )
            for line in factory.lines
        ]
        _write_csv(folder / "quality.csv", QUALITY_FIELDS, quality_rows)
        self._write_inventory(factory, day)
        (folder / "machine_status.json").write_text(
            json.dumps({"as_of": timestamp, "machines": machine_rows}, indent=2),
            encoding="utf-8",
        )

    def _transition_machine(self, factory: FactorySpec, line: LineSpec,
                            machine: MachineSpec, status: str,
                            timestamp: datetime, reason: str) -> None:
        machine_key = (factory.factory_id, machine.machine_id)
        old_status = self.status[machine_key]
        if old_status == status:
            return
        self.status[machine_key] = status
        day = timestamp.date()
        stamp = timestamp.isoformat(timespec="seconds")
        log = {
            "event_id": uuid.uuid4().hex,
            "timestamp": stamp,
            "factory_id": factory.factory_id,
            "factory_name": factory.name,
            "industry": factory.industry,
            "line_id": line.line_id,
            "line_code": line.code,
            "line_name": line.name,
            "machine_id": machine.machine_id,
            "machine_code": machine.code,
            "machine_name": machine.name,
            "machine_type": machine.machine_type,
            "from_status": old_status,
            "status": status,
            "reason": reason,
        }
        _append_jsonl(self.day_dir(factory, day) / "machine_logs.jsonl", log)

        if status == "down":
            self.open_downtime[machine_key] = {
                "event_id": log["event_id"], "factory_id": factory.factory_id,
                "line_id": line.line_id, "machine_id": machine.machine_id,
                "started_at": stamp, "ended_at": "", "duration_minutes": 0,
                "reason": reason,
            }
        elif old_status == "down":
            downtime = self.open_downtime.pop(machine_key, None)
            if downtime:
                started = datetime.fromisoformat(downtime["started_at"])
                downtime["ended_at"] = stamp
                downtime["duration_minutes"] = max(1, round((timestamp - started).total_seconds() / 60))
                self._append_downtime(factory, day, downtime)

    def _append_downtime(self, factory: FactorySpec, day: date,
                         record: dict) -> None:
        path = self.day_dir(factory, day) / "downtime.csv"
        existing = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as stream:
                existing = list(csv.DictReader(stream))
        existing.append(record)
        _write_csv(path, DOWNTIME_FIELDS, existing)

    def tick(self, now: datetime | None = None) -> None:
        """Advance all factories by one simulated production minute."""
        if now is not None:
            self.simulated_now = now
        elif self.simulated_now is None:
            self.simulated_now = datetime.now().astimezone()
        else:
            self.simulated_now += timedelta(minutes=1)
        now = self.simulated_now
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        day = now.date()
        if self.active_day is not None and self.active_day != day:
            self.achieved = {key: 0 for key in self.achieved}
            self.rate_remainder = {key: 0 for key in self.rate_remainder}
            self.line_status = {key: "running" for key in self.line_status}
            self.quality_rework = {key: 0 for key in self.quality_rework}
            self.quality_rejected = {key: 0 for key in self.quality_rejected}
            self.daily_opening_stock = dict(self.stock)
            self.daily_consumed = {key: 0.0 for key in self.daily_consumed}
            self.daily_received = {key: 0.0 for key in self.daily_received}
        self.active_day = day
        stamp = now.isoformat(timespec="seconds")
        self.tick_number += 1

        for factory in FACTORIES:
            self._process_deliveries(factory, now)
            for line in factory.lines:
                for machine in line.machines:
                    machine_id = machine.machine_id
                    machine_key = (factory.factory_id, machine_id)
                    current_status = self.status[machine_key]
                    if current_status == "down":
                        self.down_ticks[machine_key] += 1
                        # Simulated repair takes 2-5 production minutes.
                        if self.down_ticks[machine_key] >= self.random.randint(2, 5):
                            self.down_ticks[machine_key] = 0
                            self._transition_machine(factory, line, machine, "running", now,
                                                     f"maintenance completed: {machine.machine_type} serviced & recalibrated")
                    else:
                        # Realistic demo fault chance (~2% per tick across machines)
                        if self.random.random() < 0.02:
                            self.down_ticks[machine_key] = 0
                            self._transition_machine(factory, line, machine, "down", now,
                                                     factory.failure_reason)
                # Count completed products once per line, after every stage.
                # A machine outage pauses the end-of-line finished-unit count.
                line_key = (factory.factory_id, line.line_id)
                line_is_running = all(
                    self.status[(factory.factory_id, m.machine_id)] == "running"
                    for m in line.machines
                )
                if self.achieved[line_key] >= line.daily_target:
                    self.line_status[line_key] = "target_met"
                elif not line_is_running:
                    self.line_status[line_key] = "machine_down"
                elif self.random.random() < 0.98:
                    self.rate_remainder[line_key] += line.daily_target
                    units, self.rate_remainder[line_key] = divmod(self.rate_remainder[line_key], 480)
                    material_capacity = self._available_units(factory, line)
                    completed = min(units, line.daily_target - self.achieved[line_key], material_capacity)
                    if completed:
                        self._consume_materials(factory, line, completed)
                        self.achieved[line_key] += completed
                        self._record_quality(factory, line, day, stamp, completed)
                    self.line_status[line_key] = (
                        "material_shortage" if material_capacity < units else "running"
                    )
                    if self.achieved[line_key] >= line.daily_target:
                        self.line_status[line_key] = "target_met"
            self._write_current_status(factory, day, stamp)

        print(f"[{stamp}] simulator tick {self.tick_number}: "
              f"{sum(self.achieved.values())} total units achieved today")

    def run(self, *, live: bool, tick_seconds: float, history_days: int) -> None:
        if tick_seconds <= 0:
            raise ValueError("tick-seconds must be greater than zero")
        self.write_factory_metadata()
        # Use the computer's local calendar date for the factory's daily folder.
        today = datetime.now().astimezone().date()
        self.generate_history(history_days, today=today)
        self._read_today_totals(today)
        self.tick()
        if not live:
            return

        def request_stop(_signum, _frame):
            self.stop_requested = True

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)
        print(f"Live simulation running. One tick = one simulated minute; "
              f"Ctrl+C stops it. Data folder: {self.data_dir}")
        while not self.stop_requested:
            time_module.sleep(tick_seconds)
            if self.stop_requested:
                break
            self.tick()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="continue generating simulated activity")
    parser.add_argument("--tick-seconds", type=float, default=10,
                        help="real seconds between ticks in live mode (default: 10)")
    parser.add_argument("--history-days", type=int, default=0,
                        help="number of prior days to generate (default: 0; today's data is still generated)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help="where factory files should be saved")
    args = parser.parse_args()
    simulator = FactorySimulator(data_dir=args.data_dir)
    simulator.run(live=args.live, tick_seconds=args.tick_seconds,
                  history_days=args.history_days)


if __name__ == "__main__":
    main()
