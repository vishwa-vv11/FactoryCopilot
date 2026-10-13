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
    description="Structured 5-Whys methodology to trace production shortfalls to machine, quality, or material root causes.",
    trigger_keywords=["why did", "root cause", "shortfall", "missed target", "attainment", "investigate drop", "production down"],
    required_tools=["get_shortfall_analysis", "get_downtime_data", "get_quality_data", "search_factory_documents"],
    sop_procedure=(
        "1. Identify the exact line and quantity of shortfall using get_shortfall_analysis.\n"
        "2. Check if machine stoppages occurred using get_downtime_data. Note machine IDs and stoppage reasons.\n"
        "3. Check if defects or rejections occurred using get_quality_data.\n"
        "4. Search machine operating manuals with search_factory_documents to verify warning signs and recovery protocols.\n"
        "5. Connect the chain of causality: Symptom -> Direct Cause -> Contributing Factor -> Root Cause."
    ),
    output_format=(
        "- Problem Statement: Line name and shortfall units [E#].\n"
        "- Direct Observed Causes: Machine stops [E#] or quality scrap [E#].\n"
        "- Operational Guidelines: Relevant manual excerpts [E#].\n"
        "- Root Cause Determination: Definitive conclusion grounded in evidence."
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
        "compare", "yesterday", "yesterdays", "day-on-day", "shift comparison",
        "trend", "change from", "versus yesterday", "vs yesterday", "previous day",
    ],
    required_tools=["get_production_data", "get_shortfall_analysis", "get_downtime_data"],
    sop_procedure=(
        "1. Query daily production and shortfall data for the target dates using get_production_data and get_shortfall_analysis.\n"
        "2. Calculate unit output delta and attainment variance across all lines between the comparison dates.\n"
        "3. Cross-reference with get_downtime_data to determine which machine faults or stops caused any drop.\n"
        "4. Synthesize an executive variance explanation indicating which lines gained or lost output and the direct operational cause."
    ),
    output_format=(
        "- Date Comparison: Today vs Comparison Day total output variance [E#].\n"
        "- Line-by-Line Breakdown: Units achieved, shortfalls, and delta percentage [E#].\n"
        "- Operational Cause: Machine stoppages or material delays explaining the delta [E#].\n"
        "- Trend Assessment: Assessment whether the factory is recovering or experiencing ongoing disruption."
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
        }

    def match_skill(self, question: str) -> Skill | None:
        """Find the best-fitting domain skill based on semantic keywords in the question."""
        q_lower = question.lower()

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
