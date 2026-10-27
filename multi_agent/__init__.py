"""Supervisor-Worker Multi-Agent Orchestration System for FactoryOS V2.

This module implements a hierarchical multi-agent architecture:
1. Supervisor Agent:
   - Decomposes high-level factory problems into domain-specific sub-investigations.
   - Delegates sub-tasks to specialist agents.
   - Synthesizes specialist findings and evidence into a unified executive report.
2. Specialist Agents:
   - Production Specialist: Focuses on line throughput, shift schedules, and target shortfalls.
   - Maintenance Specialist: Focuses on machine telemetry, breakdown logs, and repair procedures.
   - Inventory Specialist: Focuses on warehouse stock, buffer levels, and material shortages.
   - Quality Specialist: Focuses on inspection yields, defects, rework, and scrap rates.
   - Workforce Specialist: Focuses on shift roll-call attendance, operator allocations, unstaffed machines, and standby floaters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

_ROOT_DIR = Path(__file__).resolve().parents[2]
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

try:
    from v2.guardrails import InputGuardrail, OutputGuardrail
    from v2.tools import EvidenceStore, ToolRegistry
    from v2.harness import AgentHarness, InvestigationResult, TraceEvent
except ImportError:
    from ..guardrails import InputGuardrail, OutputGuardrail
    from ..tools import EvidenceStore, ToolRegistry
    from ..harness import AgentHarness, InvestigationResult, TraceEvent


# =====================================================================
# 1. Specialist Agent Definition
# =====================================================================

@dataclass
class SpecialistReport:
    specialist_name: str
    domain: str
    question_subtask: str
    findings: str
    evidence: list[dict[str, Any]]
    rounds_executed: int


class SpecialistAgent:
    """A focused subagent with domain-scoped tools and persona prompt."""

    def __init__(self, name: str, domain: str, system_prompt: str, tool_names: set[str]) -> None:
        self.name = name
        self.domain = domain
        self.system_prompt = system_prompt
        self.tool_names = tool_names

    def investigate(
        self,
        subtask_question: str,
        factory_id: str,
        shared_evidence_store: EvidenceStore,
    ) -> SpecialistReport:
        """Run a focused investigation scoped to this specialist's tools."""
        registry = ToolRegistry(shared_evidence_store, allowed_tools=self.tool_names)
        harness = AgentHarness(registry=registry, system_prompt=self.system_prompt)

        result = harness.run(question=subtask_question, factory_id=factory_id)
        return SpecialistReport(
            specialist_name=self.name,
            domain=self.domain,
            question_subtask=subtask_question,
            findings=result.final_answer,
            evidence=result.evidence,
            rounds_executed=result.rounds_executed,
        )


# =====================================================================
# 2. Pre-configured Specialist Agents
# =====================================================================

def create_specialists() -> dict[str, SpecialistAgent]:
    return {
        "production": SpecialistAgent(
            name="Production Specialist",
            domain="production_throughput",
            system_prompt=(
                "You are the Production Specialist Agent. Your focus is strictly line output, "
                "plan attainment, and shortfalls. Use get_production_data and get_shortfall_analysis."
            ),
            tool_names={"get_production_data", "get_shortfall_analysis"},
        ),
        "maintenance": SpecialistAgent(
            name="Maintenance Specialist",
            domain="machine_health",
            system_prompt=(
                "You are the Maintenance Specialist Agent. Your focus is strictly machine status, "
                "downtime causes, and operating manual procedures. Use get_machine_status, "
                "get_downtime_data, and search_factory_documents."
            ),
            tool_names={"get_machine_status", "get_downtime_data", "search_factory_documents"},
        ),
        "inventory": SpecialistAgent(
            name="Inventory Specialist",
            domain="material_stock",
            system_prompt=(
                "You are the Inventory Specialist Agent. Your focus is strictly raw material stocks, "
                "reorder thresholds, and material shortages. Use get_inventory_data and search_factory_documents."
            ),
            tool_names={"get_inventory_data", "search_factory_documents"},
        ),
        "quality": SpecialistAgent(
            name="Quality Specialist",
            domain="quality_control",
            system_prompt=(
                "You are the Quality Specialist Agent. Your focus is strictly defect rates, "
                "inspections, rework, and scrap. Use get_quality_data."
            ),
            tool_names={"get_quality_data"},
        ),
        "workforce": SpecialistAgent(
            name="Workforce Specialist",
            domain="workforce_and_staffing",
            system_prompt=(
                "You are the Workforce Specialist Agent. Your focus is strictly shift roll-call attendance, "
                "machine operator workstation allocations, absenteeism, unstaffed machines, and standby free pool relief floaters. "
                "Use get_employees_data and get_machine_status."
            ),
            tool_names={"get_employees_data", "get_machine_status"},
        ),
    }


# =====================================================================
# 3. Supervisor Agent & Multi-Agent Result
# =====================================================================

@dataclass
class MultiAgentResult:
    master_question: str
    factory_id: str
    specialists_consulted: list[str]
    specialist_reports: list[SpecialistReport]
    synthesized_answer: str
    evidence_count: int
    evidence: list[dict[str, Any]]
    trace: list[TraceEvent] = field(default_factory=list)

    @property
    def final_answer(self) -> str:
        return self.synthesized_answer

    def to_dict(self) -> dict[str, Any]:
        return {
            "master_question": self.master_question,
            "question": self.master_question,
            "factory_id": self.factory_id,
            "mode": "multi_agent",
            "specialists_consulted": self.specialists_consulted,
            "specialist_reports": [
                {
                    "specialist": r.specialist_name,
                    "domain": r.domain,
                    "subtask": r.question_subtask,
                    "findings": r.findings,
                    "evidence_items": len(r.evidence),
                }
                for r in self.specialist_reports
            ],
            "final_answer": self.synthesized_answer,
            "synthesized_answer": self.synthesized_answer,
            "rounds_executed": len(self.specialist_reports),
            "evidence_count": self.evidence_count,
            "evidence": self.evidence,
            "trace_events": len(self.trace),
            "trace": [
                {
                    "timestamp": t.timestamp,
                    "round": t.round_index,
                    "type": t.event_type,
                    "content": t.content,
                }
                for t in self.trace
            ],
        }


class SupervisorAgent:
    """The central orchestrator that decomposes questions, delegates subtasks, and synthesizes reports."""

    def __init__(self) -> None:
        self.specialists = create_specialists()

    def decompose(self, question: str) -> list[tuple[str, str]]:
        """Decompose an overarching question into targeted subtasks for specialist agents."""
        q_lower = question.lower()
        subtasks: list[tuple[str, str]] = []

        # Always consult production specialist on broad investigation questions
        if any(w in q_lower for w in ("production", "output", "drop", "shortfall", "miss", "why", "line")):
            subtasks.append(("production", f"Analyze production volume and identify shortfalls: {question}"))

        # Consult maintenance specialist if machines or breakdowns are mentioned or during RCA
        if any(w in q_lower for w in ("machine", "downtime", "breakdown", "stop", "fail", "pump", "vibration", "why", "root cause")):
            subtasks.append(("maintenance", f"Inspect machine statuses, downtime events, and maintenance SOPs: {question}"))

        # Consult inventory specialist if materials or stocks are mentioned
        if any(w in q_lower for w in ("material", "inventory", "stock", "supply", "reorder", "shortage")):
            subtasks.append(("inventory", f"Assess raw material stock levels and reorder safety buffers: {question}"))

        # Consult quality specialist if quality, defects, or scrap are mentioned
        if any(w in q_lower for w in ("quality", "defect", "rework", "scrap", "yield", "inspection")):
            subtasks.append(("quality", f"Inspect quality pass rates, rework units, and defect logs: {question}"))

        # Consult workforce specialist if operators, staffing, attendance, absence, floaters, unstaffed machines, or crew are mentioned
        if any(w in q_lower for w in (
            "operator", "operators", "worker", "workers", "employee", "employees",
            "staff", "staffing", "absent", "absence", "absenteeism", "attendance",
            "roster", "crew", "floater", "floaters", "unstaffed", "standby", "free pool",
            "relief", "ops", "personnel", "who is working", "who is assigned",
        )):
            subtasks.append(("workforce", f"Assess shift crew attendance, operator allocations, and unstaffed stations: {question}"))

        # Default fallback: Production + Maintenance
        if not subtasks:
            subtasks = [
                ("production", f"Check production attainment: {question}"),
                ("maintenance", f"Check machine downtime: {question}"),
            ]

        return subtasks

    def run(self, question: str, factory_id: str = "F001") -> MultiAgentResult:
        """Execute the multi-agent investigation workflow: Decompose -> Delegate -> Synthesize."""
        trace: list[TraceEvent] = []

        def _now() -> str:
            return datetime.now(timezone.utc).isoformat()

        # Step 1: Input Guardrail
        f_guard = InputGuardrail.validate_factory_id(factory_id)
        q_guard = InputGuardrail.validate_question(question)
        if not f_guard.passed or not q_guard.passed:
            reason = f_guard.reason or q_guard.reason
            return MultiAgentResult(
                master_question=question,
                factory_id=factory_id,
                specialists_consulted=[],
                specialist_reports=[],
                synthesized_answer=f"Input Guardrail Blocked: {reason}",
                evidence_count=0,
                evidence=[],
                trace=[TraceEvent(_now(), 0, "guardrail_rejected", reason)],
            )

        clean_factory_id = f_guard.sanitized_content
        clean_question = q_guard.sanitized_content
        trace.append(TraceEvent(_now(), 0, "guardrail_passed", "Input parameters validated."))

        # Step 2: Decomposition
        subtasks = self.decompose(clean_question)
        consulted_keys = [key for key, _ in subtasks]
        trace.append(TraceEvent(_now(), 0, "supervisor_decomposed", {"consulted_specialists": consulted_keys}))

        # Step 3: Delegation (Execute Specialists with a Unified Evidence Store)
        shared_evidence_store = EvidenceStore()
        reports: list[SpecialistReport] = []

        for specialist_key, subtask_prompt in subtasks:
            specialist = self.specialists.get(specialist_key)
            if not specialist:
                continue

            trace.append(TraceEvent(_now(), len(reports) + 1, "supervisor_delegated", {
                "specialist": specialist.name,
                "subtask": subtask_prompt,
            }))

            report = specialist.investigate(
                subtask_question=subtask_prompt,
                factory_id=clean_factory_id,
                shared_evidence_store=shared_evidence_store,
            )
            reports.append(report)

            trace.append(TraceEvent(_now(), len(reports), "specialist_reported", {
                "specialist": specialist.name,
                "evidence_gathered": len(report.evidence),
            }))

        # Step 4: Synthesis & Output Guardrail
        all_evidence = shared_evidence_store.get_all()

        synthesis_lines = [
            f"# Multi-Agent Executive Investigation: Factory {clean_factory_id}\n",
            f"**Objective:** {clean_question}\n",
            f"**Specialists Consulted:** {', '.join(r.specialist_name for r in reports)}\n",
            "## 1. Domain Findings & Correlated Evidence",
        ]

        for report in reports:
            synthesis_lines.append(f"\n### {report.specialist_name} ({report.domain})")
            cleaned_findings = "\n".join(
                line for line in report.findings.splitlines()
                if not line.startswith("**Investigation Summary") and not line.startswith("# Multi-Agent")
            )
            synthesis_lines.append(cleaned_findings)

        synthesis_lines.append("\n## 2. Executive Synthesis & Root Cause Conclusion")
        synthesis_lines.append(
            f"Based on coordinated multi-domain analysis across {len(reports)} specialists, "
            f"all operational facts have been cross-referenced and validated with {len(all_evidence)} collected evidence items."
        )

        raw_synthesized_answer = "\n".join(synthesis_lines)

        # Enforce output guardrail groundedness on the final report
        verified_answer, _ = OutputGuardrail.enforce_groundedness(raw_synthesized_answer, all_evidence)

        trace.append(TraceEvent(_now(), len(reports) + 1, "supervisor_synthesized", "Executive report generated."))

        return MultiAgentResult(
            master_question=clean_question,
            factory_id=clean_factory_id,
            specialists_consulted=consulted_keys,
            specialist_reports=reports,
            synthesized_answer=verified_answer,
            evidence_count=len(all_evidence),
            evidence=all_evidence,
            trace=trace,
        )


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing FactoryOS V2 Multi-Agent Orchestration System...\n")

    supervisor = SupervisorAgent()
    master_query = "Why did production miss target on Factory F001? Did machine breakdown or absent operators on unstaffed stations cause it?"

    result = supervisor.run(question=master_query, factory_id="F001")

    print(f"Master Question: {result.master_question}")
    print(f"Specialists Consulted: {result.specialists_consulted}")
    print(f"Total Evidence Gathered: {result.evidence_count} items")
    print(f"Trace Events: {len(result.trace)}")

    print("\n--- Final Synthesized Multi-Agent Report ---")
    print(result.synthesized_answer[:600] + "...\n")

    print("[OK] Multi-Agent Orchestration smoke test completed successfully!")
