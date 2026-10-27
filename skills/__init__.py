"""Modular Domain Skills Subsystem for FactoryOS V2.

Skills are standardized operational playbooks (SOPs) that teach an agent
how to conduct structured investigations for specific problem categories.

Components:
1. Skill Specification: Defines workflow steps, required tools, and prompt injection SOP.
2. Skill Registry: Automatically matches incoming questions to relevant domain playbooks.
3. Pre-built Skills:
   - Root Cause Analysis (RCA) Skill
   - Inventory & Supply Shortage Skill
   - Preventative Maintenance & Safety Skill
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass
class Skill:
    """A domain capability package defining procedural workflow steps."""
    name: str
    description: str
    trigger_keywords: list[str]
    required_tools: list[str]
    sop_procedure: str
    output_format: str

    def format_prompt_injection(self) -> str:
        """Generate the procedural instructions to inject into the LLM system prompt."""
        tools_list = ", ".join(self.required_tools)
        return (
            f"\n\n--- ACTIVE DOMAIN SKILL: {self.name.upper()} ---\n"
            f"Description: {self.description}\n"
            f"Recommended Tools: {tools_list}\n\n"
            f"Standard Operating Procedure (SOP):\n{self.sop_procedure}\n\n"
            f"Required Output Format:\n{self.output_format}\n"
            f"-----------------------------------------\n"
        )


# =====================================================================
# Pre-built Domain Skills
# =====================================================================

RCA_SKILL = Skill(
    name="Root Cause Analysis (RCA)",
    description="Structured 5-Whys methodology to trace production shortfalls and line stoppages to staffing bottlenecks, machine downtime, quality defects, or material starvation.",
    trigger_keywords=["why did", "root cause", "shortfall", "missed target", "attainment", "investigate drop", "production down", "halt", "halted", "stopped", "why stopped", "line stopped", "why is line", "why did line halt"],
    required_tools=["get_shortfall_analysis", "get_employees_data", "get_machine_status", "get_downtime_data", "get_quality_data", "search_factory_documents"],
    sop_procedure=(
        "1. Identify the exact line status and operational disruption using get_shortfall_analysis and get_machine_status.\n"
        "2. CHECK STAFFING FIRST: If get_shortfall_analysis or get_employees_data reports an UNSTAFFED BOTTLENECK (an operator is absent with NO relief floater assigned), conclude definitively that the line is halted/slowed due to the UNSTAFFED WORKSTATION! Do NOT search for or invent mechanical coolant faults when an unstaffed operator bottleneck is present.\n"
        "3. Only if all workstations are fully staffed, check get_downtime_data for active mechanical stoppages.\n"
        "4. Never output or cite raw hex event IDs. Always use evidence citations like [E1], [E2].\n"
        "5. Connect the chain of causality: Symptom -> Direct Cause -> Contributing Factor -> Root Cause."
    ),
    output_format=(
        "Keep your response strictly to 1 or 2 concise lines (no boilerplate headings, no repeating the explanation):\n"
        "State directly why the line is halted/slowed (naming the unstaffed workstation, machine ID, and absent operator [E#]), followed by the single immediate action needed."
    ),
)

INVENTORY_SKILL = Skill(
    name="Material Shortage & Supply Chain Assessment",
    description="Evaluates raw material inventory, buffer levels, and supplier delivery risks.",
    trigger_keywords=["inventory", "material", "stock", "reorder", "supplier", "shortage", "raw material"],
    required_tools=["get_inventory_data", "get_shortfall_analysis", "search_factory_documents"],
    sop_procedure=(
        "1. Inspect warehouse stock and reorder levels using get_inventory_data.\n"
        "2. Identify materials where available stock is below or near the safety reorder threshold.\n"
        "3. Correlate with get_shortfall_analysis to see if any production lines were starved of materials.\n"
        "4. Check shared safety guidelines for material receipt verification protocols."
    ),
    output_format=(
        "- Material Position: Current available stock vs reorder threshold [E#].\n"
        "- Production Impact: Lines affected by material starvation [E#].\n"
        "- Recommended Action: Expedited reorder or buffer adjustment."
    ),
)

MAINTENANCE_SKILL = Skill(
    name="Preventative Maintenance & Equipment Health",
    description="Assesses machine operational status, stoppage duration, and compliance with maintenance SOPs.",
    trigger_keywords=["maintenance", "machine health", "breakdown", "downtime", "pump", "spindle", "vibration", "service", "preventative"],
    required_tools=["get_machine_status", "get_downtime_data", "search_factory_documents"],
    sop_procedure=(
        "1. Inspect live machine operational statuses (Running, Down, Idle) using get_machine_status.\n"
        "2. Query historical downtime durations and reason codes using get_downtime_data.\n"
        "3. Search maintenance and safety manuals with search_factory_documents for restart checklists.\n"
        "4. Verify whether equipment can be safely returned to production."
    ),
    output_format=(
        "- Equipment Status: Machine identifier, status, and telemetry [E#].\n"
        "- Downtime Record: Duration and failure reason [E#].\n"
        "- Safety & Restart SOP: Required technician procedures before restart [E#]."
    ),
)

DAY_COMPARISON_SKILL = Skill(
    name="Day-on-Day Shift & Trend Comparison",
    description="Compares production output, plan attainment, and machine losses between yesterday and today or across historical dates.",
    trigger_keywords=[
        "compare", "day-on-day", "shift comparison",
        "trend", "change from", "versus yesterday", "vs yesterday", "variance between",
    ],
    required_tools=["get_production_data", "get_shortfall_analysis", "get_downtime_data"],
    sop_procedure=(
        "1. Focus directly on the specific line or date variance requested by the user.\n"
        "2. Query daily production data for the target dates using get_production_data.\n"
        "3. Provide the requested output units, target, and delta concisely without dumping unrequested line details."
    ),
    output_format=(
        "Directly answer the comparison or date query requested:\n"
        "- State the achieved units and delta for the requested line/dates [E#].\n"
        "Do NOT attach unsolicited factory-wide trend essays unless explicitly asked."
    ),
)

WORKFORCE_SKILL = Skill(
    name="Shift Workforce Staffing & Attendance Management",
    description="Analyzes shift roll-call attendance, operator workstation assignments, absenteeism, unstaffed bottleneck machines, and standby relief floaters.",
    trigger_keywords=[
        "operator", "operators", "worker", "workers", "employee", "employees",
        "staff", "staffing", "absent", "absence", "absenteeism", "attendance",
        "roster", "crew", "floater", "floaters", "unstaffed", "standby", "free pool",
        "relief", "who is working", "ops", "personnel", "replace", "replaced", "replacement",
        "replacing", "cover", "covering", "coverage",
    ],
    required_tools=["get_employees_data", "get_machine_status"],
    sop_procedure=(
        "1. Focus directly and specifically on the workforce question asked by the user.\n"
        "2. If asked who is replaced or who replaced absent operators, list the relief floaters covering for absent operators strictly from the tool evidence (e.g. Floater Name [ID] covering for Absent Operator [ID]), and note if any absent operator is unstaffed without coverage.\n"
        "3. If asked who is absent, state the actual count and list the absent operators found in the tool evidence with their workstations and line names. Never mention or hallucinate names not present in the tool evidence.\n"
        "4. Do NOT confuse absent operators with replacement floaters. Clearly distinguish between who is absent and who replaced them.\n"
        "5. If asked about Free Pool or available floaters, report only unassigned operators who are on standby in the Free Pool. If a floater is deployed to a machine, report that they are actively deployed covering that machine, NOT available in the Free Pool. Never claim a standby operator is assigned to a machine."
    ),
    output_format=(
        "Respond concisely to the specific workforce query:\n"
        "- If asked about replacements: State which relief floater replaced which absent operator [E#]\n"
        "- If asked about absentees: State total count and list absent operators with Name, ID, Workstation, and Line [E#]\n"
        "- If asked about Free Pool / floaters: List available standby operators, and separate any actively deployed floaters covering machines [E#]\n"
        "Keep it direct and accurate."
    ),
)


ORDER_SKILL = Skill(
    name="Manufacturing Work Order & Queue Management",
    description="Tracks work order progress, completion status, sequential order IDs (ORD-001, ORD-002, etc.), queued next orders, and order dispatching.",
    trigger_keywords=[
        "order", "orders", "next order", "order id", "unique id",
        "work order", "batch completed", "order completed", "queued order",
        "dispatch", "load order", "ord-", "ord ", "ord", "ends", "end", "complete", "finish",
    ],
    required_tools=["get_orders_data", "get_production_data"],
    sop_procedure=(
        "1. Check the status of current and queued work orders using get_orders_data.\n"
        "2. If an order on a line is completed (100% quota achieved), identify the next sequential queued order for that line.\n"
        "3. Explain which order is currently active, which orders are completed, and the unique ID of the next queued order.\n"
        "4. Note that orders progress sequentially (e.g. ORD-001, ORD-002, ORD-003 -> ORD-004, ORD-005, etc.) so each order maintains a unique traceable ID across days."
    ),
    output_format=(
        "Respond concisely regarding the requested work order:\n"
        "- Active / Completed Order: ID, customer, product, and progress [E#]\n"
        "- Next Sequential Order: Next queued order ID and ready status [E#]"
    ),
)


# =====================================================================
# Skill Registry
# =====================================================================

class SkillRegistry:
    """Dispatches and manages domain skills for investigations."""

    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {
            "rca": RCA_SKILL,
            "inventory": INVENTORY_SKILL,
            "maintenance": MAINTENANCE_SKILL,
            "comparison": DAY_COMPARISON_SKILL,
            "workforce": WORKFORCE_SKILL,
            "order": ORDER_SKILL,
        }

    def match_skill(self, question: str) -> Skill | None:
        """Find the best-fitting domain skill based on semantic keywords in the question."""
        q_lower = question.lower()

        # Direct pattern match for work order codes like "ord 12", "ord-012", "order 7"
        if re.search(r"\b(?:ord(?:er)?[-_\s]*\d+)\b", q_lower):
            return ORDER_SKILL

        # Score skills based on keyword hits
        best_skill = None
        best_score = 0

        for skill in self.skills.values():
            score = sum(1 for kw in skill.trigger_keywords if kw in q_lower)
            if score > best_score:
                best_score = score
                best_skill = skill

        # Default to RCA Skill if it's an investigative question
        if not best_skill and any(w in q_lower for w in ("why", "investigate", "miss", "problem", "drop")):
            return RCA_SKILL

        return best_skill

    def list_skills(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "required_tools": s.required_tools,
            }
            for s in self.skills.values()
        ]


# Singleton instance
SKILL_REGISTRY = SkillRegistry()


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing FactoryOS V2 Domain Skills System...\n")

    registry = SkillRegistry()
    print(f"Available Skills: {[s['name'] for s in registry.list_skills()]}")

    # Test 1: Match RCA Skill
    q1 = "Why did Line 1 miss its production target today?"
    matched1 = registry.match_skill(q1)
    print(f"\nQuery: '{q1}'")
    print(f"Matched Skill: {matched1.name if matched1 else 'None'}")
    assert matched1 == RCA_SKILL, "Expected RCA Skill match"

    # Test 2: Match Inventory Skill
    q2 = "Check raw material stock levels and low inventory"
    matched2 = registry.match_skill(q2)
    print(f"\nQuery: '{q2}'")
    print(f"Matched Skill: {matched2.name if matched2 else 'None'}")
    assert matched2 == INVENTORY_SKILL, "Expected Inventory Skill match"

    # Test 3: Match Maintenance Skill
    q3 = "What is the procedure when pump vibration causes machine breakdown?"
    matched3 = registry.match_skill(q3)
    print(f"\nQuery: '{q3}'")
    print(f"Matched Skill: {matched3.name if matched3 else 'None'}")
    assert matched3 == MAINTENANCE_SKILL, "Expected Maintenance Skill match"

    print("\n--- Sample Skill Prompt Injection ---")
    print(matched1.format_prompt_injection()[:300] + "...")

    print("\n[OK] Domain Skills smoke test completed successfully!")
