"""Agent Harness (Runtime & Execution Engine) for FactoryOS V2.

The Harness surrounds the LLM with an operational scaffolding:
1. ReAct Execution Loop: Manages Think -> Tool Call -> Observe cycles up to MAX_ROUNDS.
2. Memory & State: Tracks message history, accumulated evidence, and execution trace events.
3. Environment Interfacing: Executes tool calls via ToolRegistry.
4. Dual Mode:
   - Live LLM Mode: Connects to any OpenAI-compatible provider (OpenAI, Ollama, Groq, OpenRouter).
   - Deterministic Fallback Mode: Runs an offline, evidence-gathering investigation when no API key is set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .tools import EvidenceStore, ToolRegistry
from .guardrails import InputGuardrail, ExecutionGuardrail, OutputGuardrail
from .skills import SKILL_REGISTRY, Skill

MAX_TOOL_ROUNDS = 5
REQUEST_TIMEOUT_SECONDS = 45

SYSTEM_PROMPT = """You are FactoryOS, an autonomous industrial operations analyst.
Your objective is to investigate operational problems, production shortfalls, machine breakdowns, and material delays.

Strict Operating Rules:
1. Use only the provided read-only tools to gather facts. Never assume facts not returned by tools.
2. Every factual statement in your answer MUST cite an evidence ID using square brackets, e.g., [E1], [E2].
3. Distinguish confirmed observations from speculative hypotheses.
4. If a line missed its target, correlate the shortfall with downtime, low inventory, or quality defects.
5. If evidence is missing or a tool returns no records, state this explicitly.
"""


# =====================================================================
# 1. Trace & Result Data Models
# =====================================================================

@dataclass
class TraceEvent:
    timestamp: str
    round_index: int
    event_type: str  # 'thought', 'tool_call', 'observation', 'final_answer'
    content: Any


@dataclass
class InvestigationResult:
    question: str
    factory_id: str
    mode: str  # 'llm' or 'deterministic'
    rounds_executed: int
    final_answer: str
    evidence: list[dict[str, Any]]
    skill_name: str | None = None
    trace: list[TraceEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "factory_id": self.factory_id,
            "mode": self.mode,
            "skill": self.skill_name,
            "rounds_executed": self.rounds_executed,
            "final_answer": self.final_answer,
            "evidence_count": len(self.evidence),
            "evidence": self.evidence,
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


# =====================================================================
# 2. LLM Communication Layer (OpenAI-Compatible HTTP Client)
# =====================================================================

import socket

def _is_local_endpoint_alive(host: str = "127.0.0.1", port: int = 11434) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except (OSError, TimeoutError):
        return False

def _call_llm(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Call an OpenAI-compatible /v1/chat/completions endpoint (supports OpenAI, Ollama, Groq, etc.)."""
    base_url = os.getenv("LLM_API_BASE_URL", "").rstrip("/")
    is_ollama = "11434" in base_url or os.getenv("LLM_PROVIDER", "").lower() == "ollama"
    
    if is_ollama and not _is_local_endpoint_alive():
        raise RuntimeError("Local Ollama service (127.0.0.1:11434) is not running.")

    if not base_url:
        base_url = "http://localhost:11434/v1" if is_ollama else "https://api.openai.com/v1"

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ("ollama" if is_ollama else None)
    default_model = "llama3.2" if is_ollama else "gpt-4o-mini"
    model = os.getenv("LLM_MODEL", default_model)

    if not api_key:
        raise RuntimeError("LLM_API_KEY is not set (and no local Ollama endpoint detected).")

    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.1,
    }

    req = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    timeout_sec = int(os.getenv("LLM_TIMEOUT_SECONDS", "4" if is_ollama else "30"))
    try:
        with urlopen(req, timeout=timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        err_msg = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API returned HTTP {exc.code}: {err_msg}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"LLM API network error: {exc}") from exc


# =====================================================================
# 3. Deterministic Investigation Engine (Offline Fallback)
# =====================================================================

def _run_deterministic_investigation(
    question: str,
    factory_id: str,
    registry: ToolRegistry,
    trace: list[TraceEvent],
) -> tuple[str, int]:
    """Execute rule-based tool calls and synthesize an evidence-cited answer without LLM credentials."""
    q_lower = question.lower()
    round_idx = 1

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    trace.append(TraceEvent(_now(), round_idx, "thought", "Running in deterministic mode. Analyzing question intent."))

    # Step 1: Always check shortfall analysis first
    trace.append(TraceEvent(_now(), round_idx, "tool_call", {"tool": "get_shortfall_analysis", "factory_id": factory_id}))
    res = registry.execute("get_shortfall_analysis", {"factory_id": factory_id})
    trace.append(TraceEvent(_now(), round_idx, "observation", f"Found {len(res['result'])} lines."))

    # Step 2: If question mentions downtime or machines, query downtime
    if any(w in q_lower for w in ("downtime", "breakdown", "machine", "stop", "fail")):
        round_idx += 1
        trace.append(TraceEvent(_now(), round_idx, "tool_call", {"tool": "get_downtime_data", "factory_id": factory_id}))
        dt_res = registry.execute("get_downtime_data", {"factory_id": factory_id})
        trace.append(TraceEvent(_now(), round_idx, "observation", f"Found {len(dt_res['result'])} downtime events."))

    # Step 3: If question mentions inventory or material, query inventory
    if any(w in q_lower for w in ("inventory", "material", "stock", "supply")):
        round_idx += 1
        trace.append(TraceEvent(_now(), round_idx, "tool_call", {"tool": "get_inventory_data", "factory_id": factory_id}))
        inv_res = registry.execute("get_inventory_data", {"factory_id": factory_id})
        trace.append(TraceEvent(_now(), round_idx, "observation", f"Found {len(inv_res['result'])} inventory records."))

    # Synthesize answer citing collected evidence
    evidence = registry.evidence_store.get_all()
    if not evidence:
        return "No operational records were found for the requested factory.", round_idx

    answer_parts = [
        f"**Investigation Summary for Factory {factory_id}** (Deterministic Evidence Correlation):\n"
    ]

    shortfall_items = [e for e in evidence if e["tool"] == "get_shortfall_analysis"]
    for item in shortfall_items:
        data = item["data"]
        shortfall = int(data.get("shortfall_units") or 0)
        line_name = data.get("line_name", data.get("line_id"))
        ev_id = f"[{item['evidence_id']}]"

        if shortfall > 0:
            signals = "; ".join(data.get("signals", []))
            answer_parts.append(
                f"- **{line_name}** missed its target with a shortfall of **{shortfall} units** {ev_id}. Observed root-cause signals: {signals}."
            )
        else:
            answer_parts.append(
                f"- **{line_name}** met its daily target successfully {ev_id}."
            )

    # Step 4: If question relates to procedures, manuals, safety or maintenance SOPs, search documents
    if any(w in q_lower for w in ("sop", "procedure", "manual", "guide", "policy", "coolant", "vibration", "recover", "safety")):
        round_idx += 1
        trace.append(TraceEvent(_now(), round_idx, "tool_call", {"tool": "search_factory_documents", "query": question, "factory_id": factory_id}))
        doc_res = registry.execute("search_factory_documents", {"factory_id": factory_id, "query": question, "limit": 2})
        trace.append(TraceEvent(_now(), round_idx, "observation", f"Found {len(doc_res['result'])} document reference(s)."))

    downtime_items = [e for e in evidence if e["tool"] == "get_downtime_data"]
    if downtime_items:
        answer_parts.append("\n**Specific Machine Downtime Events:**")
        for item in downtime_items[:3]:
            d = item["data"]
            answer_parts.append(
                f"- Machine `{d.get('machine_id')}`: {d.get('duration_minutes')} mins stoppage due to *{d.get('reason')}* [{item['evidence_id']}]."
            )
    inventory_items = [e for e in evidence if e["tool"] == "get_inventory_data"]
    if inventory_items:
        answer_parts.append("\n**Material Inventory Position & Stock Levels:**")
        for item in inventory_items[:4]:
            d = item["data"]
            answer_parts.append(
                f"- Material `{d.get('material_name')}`: {d.get('available_quantity')} {d.get('unit')} in stock (Reorder threshold: {d.get('reorder_level')}, Status: {d.get('status')}) [{item['evidence_id']}]."
            )

    doc_items = [e for e in evidence if e["tool"] == "search_factory_documents"]
    if doc_items:
        answer_parts.append("\n**Relevant Operating Procedures & Guidelines:**")
        for item in doc_items:
            d = item["data"]
            first_line = d.get("content", "").split("\n")[0].strip("# ")
            answer_parts.append(
                f"- Document `{d.get('source')}` ({first_line}): *{item['detail'][:140]}...* [{item['evidence_id']}]."
            )

    answer_parts.append("\n*(Report produced deterministically from validated local operational records)*")
    final_text = "\n".join(answer_parts)
    trace.append(TraceEvent(_now(), round_idx, "final_answer", final_text))
    return final_text, round_idx


# =====================================================================
# 4. The Agent Harness Runtime
# =====================================================================

class AgentHarness:
    """The runtime orchestrator for autonomous operations investigations."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.evidence_store = EvidenceStore()
        self.registry = registry or ToolRegistry(self.evidence_store)

    def run(self, question: str, factory_id: str = "F001") -> InvestigationResult:
        trace: list[TraceEvent] = []
        base_url = os.getenv("LLM_API_BASE_URL", "").rstrip("/")
        is_ollama = "11434" in base_url or os.getenv("LLM_PROVIDER", "").lower() == "ollama"
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ("ollama" if is_ollama else None)

        def _now() -> str:
            return datetime.now(timezone.utc).isoformat()

        # Step 1: Input Guardrails Enforcement
        f_guard = InputGuardrail.validate_factory_id(factory_id)
        if not f_guard.passed:
            return InvestigationResult(
                question=question,
                factory_id=factory_id,
                mode="guardrail_blocked",
                rounds_executed=0,
                final_answer=f"Input Guardrail Violation: {f_guard.reason}",
                evidence=[],
                trace=[TraceEvent(_now(), 0, "guardrail_rejected", f_guard.reason)],
            )

        q_guard = InputGuardrail.validate_question(question)
        if not q_guard.passed:
            return InvestigationResult(
                question=question,
                factory_id=factory_id,
                mode="guardrail_blocked",
                rounds_executed=0,
                final_answer=f"Input Guardrail Violation: {q_guard.reason}",
                evidence=[],
                trace=[TraceEvent(_now(), 0, "guardrail_rejected", q_guard.reason)],
            )

        clean_question = q_guard.sanitized_content
        clean_factory_id = f_guard.sanitized_content
        trace.append(TraceEvent(_now(), 0, "guardrail_passed", "Input parameters verified successfully."))

        # Step 2: Dynamic Skill Discovery & SOP Matching
        matched_skill = SKILL_REGISTRY.match_skill(clean_question)
        skill_name = matched_skill.name if matched_skill else None
        if matched_skill:
            trace.append(TraceEvent(_now(), 0, "skill_activated", f"Matched playbook: {matched_skill.name}"))

        # Step 3: Deterministic fallback when no API key is provided
        if not api_key:
            raw_answer, rounds = _run_deterministic_investigation(
                question=clean_question,
                factory_id=clean_factory_id,
                registry=self.registry,
                trace=trace,
            )
            # Output Guardrail Groundedness Check
            final_answer, grounded = OutputGuardrail.enforce_groundedness(raw_answer, self.evidence_store.get_all())
            return InvestigationResult(
                question=clean_question,
                factory_id=clean_factory_id,
                mode="deterministic",
                skill_name=skill_name,
                rounds_executed=rounds,
                final_answer=final_answer,
                evidence=self.evidence_store.get_all(),
                trace=trace,
            )

        # Step 4: Live LLM ReAct Loop with Skill SOP Injection
        active_system_prompt = SYSTEM_PROMPT
        if matched_skill:
            active_system_prompt += matched_skill.format_prompt_injection()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": active_system_prompt},
            {
                "role": "user",
                "content": f"Factory ID: {clean_factory_id}\nQuestion: {clean_question}\nPlease investigate using the available read-only tools and cite evidence IDs.",
            },
        ]

        allowed_tools = {s["function"]["name"] for s in self.registry.schemas}
        round_index = 0
        final_answer = ""

        while round_index < MAX_TOOL_ROUNDS:
            round_index += 1
            trace.append(TraceEvent(_now(), round_index, "thought", f"Requesting LLM round {round_index}"))

            try:
                response = _call_llm(messages=messages, tools=self.registry.schemas)
            except RuntimeError as exc:
                trace.append(TraceEvent(_now(), round_index, "thought", f"LLM connection error: {exc}. Falling back to deterministic engine."))
                raw_answer, rounds = _run_deterministic_investigation(
                    question=clean_question,
                    factory_id=clean_factory_id,
                    registry=self.registry,
                    trace=trace,
                )
                final_answer, _ = OutputGuardrail.enforce_groundedness(raw_answer, self.evidence_store.get_all())
                return InvestigationResult(
                    question=clean_question,
                    factory_id=clean_factory_id,
                    mode="deterministic_fallback",
                    skill_name=skill_name,
                    rounds_executed=rounds,
                    final_answer=final_answer,
                    evidence=self.evidence_store.get_all(),
                    trace=trace,
                )
            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls", [])

            # Case A: Model returned a final textual answer without tool calls
            if not tool_calls:
                raw_answer = message.get("content", "")
                final_answer, _ = OutputGuardrail.enforce_groundedness(raw_answer, self.evidence_store.get_all())
                trace.append(TraceEvent(_now(), round_index, "final_answer", final_answer))
                break

            # Case B: Model requested one or more tool calls
            messages.append(message)  # Store assistant response with tool calls

            for call in tool_calls:
                func = call.get("function", {})
                tool_name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # Always force factory scoping to prevent cross-factory pollution
                args["factory_id"] = clean_factory_id

                # Operational Guardrail check
                exec_guard = ExecutionGuardrail.validate_tool_call(tool_name, args, allowed_tools)
                if not exec_guard.passed:
                    trace.append(TraceEvent(_now(), round_index, "guardrail_rejected", exec_guard.reason))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": tool_name,
                        "content": json.dumps({"error": exec_guard.reason}),
                    })
                    continue

                trace.append(TraceEvent(_now(), round_index, "tool_call", {"tool": tool_name, "args": args}))

                try:
                    execution_result = self.registry.execute(tool_name, args)
                    observation = execution_result["result"]
                    ev_ids = execution_result["evidence_ids"]
                    content_str = json.dumps({"evidence_ids": ev_ids, "data": observation}, ensure_ascii=False)
                except Exception as exc:
                    content_str = json.dumps({"error": str(exc)})
                    observation = {"error": str(exc)}

                trace.append(TraceEvent(_now(), round_index, "observation", observation))

                # Append tool observation to conversation history
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": tool_name,
                    "content": content_str,
                })

        if not final_answer:
            raw_answer = "Investigation reached the maximum allowed execution rounds. Accumulated evidence:\n" + self.evidence_store.summary()
            final_answer, _ = OutputGuardrail.enforce_groundedness(raw_answer, self.evidence_store.get_all())

        return InvestigationResult(
            question=clean_question,
            factory_id=clean_factory_id,
            mode="llm",
            skill_name=skill_name,
            rounds_executed=round_index,
            final_answer=final_answer,
            evidence=self.evidence_store.get_all(),
            trace=trace,
        )


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing FactoryOS V2 Agent Harness & ReAct Loop...\n")

    harness = AgentHarness()
    sample_query = "Why did production miss target on factory F001? What does the manual procedure say about coolant pump vibration?"
    result = harness.run(question=sample_query, factory_id="F001")

    print(f"Mode: {result.mode}")
    print(f"Rounds executed: {result.rounds_executed}")
    print(f"Evidence collected: {len(result.evidence)} items")
    print("\n--- Final Answer ---")
    print(result.final_answer)

    print("\n--- Execution Trace Sample (First 3 events) ---")
    for event in result.trace[:3]:
        print(f"[{event.timestamp}] Round {event.round_index} ({event.event_type}): {event.content}")

    print("\n[OK] Agent Harness smoke test completed successfully!")
