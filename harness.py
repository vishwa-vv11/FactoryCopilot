"""Agent Harness (Runtime & Execution Engine) for FactoryOS V2.

The Harness surrounds Ollama with an autonomous operational scaffolding:
1. ReAct Execution Loop: Manages Think -> Tool Call -> Observe cycles up to MAX_ROUNDS.
2. Memory & State: Tracks message history, accumulated evidence, and execution trace events.
3. Environment Interfacing: Executes tool calls via ToolRegistry.
4. Ollama Exclusive: Directly interfaces with local Ollama service without deterministic fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .tools import EvidenceStore, ToolRegistry, normalize_factory_id, DATA_DIR
from .guardrails import InputGuardrail, ExecutionGuardrail, OutputGuardrail
from .skills import SKILL_REGISTRY, Skill

MAX_TOOL_ROUNDS = 5
REQUEST_TIMEOUT_SECONDS = 120

SYSTEM_PROMPT = """You are Macho, an autonomous industrial operations AI copilot powered by Ollama.
Your objective is to answer questions about factory operations, production output, machine telemetry, workforce staffing, inventory, and quality.

Strict Operating Rules:
1. Answer ONLY what the user asked. Be direct, precise, and concise. Do NOT generate unsolicited observations, unrelated line summaries, or extraneous context that the user did not ask for.
2. If the user asks a conversational question, capability question (e.g. "what can you do", "who are you", "help"), or greeting, answer conversationally describing your assistant capabilities without calling operational tools or dumping factory targets.
3. When gathering operational data, use only the provided tools. Every factual statement derived from tool data MUST cite an evidence ID using square brackets, e.g., [E1], [E2].
4. Do NOT call get_factory_overview unless the user explicitly asks for an overview of the entire factory. For specific lines, queries, or dates, use the specific tool (e.g. get_production_data for production numbers).
5. If a tool returns no data or records, state this directly without making up facts.
6. When asked when a machine was repaired, serviced, or maintained, call get_machine_logs with event_type='repair' and machine_id (e.g. machine_id='L01-M01'). State the exact repair date and time directly and cite the evidence tag [E#].
7. When asked about manufacturing orders, order completion, or next queued orders, use get_orders_data. Explain order status, unique sequential order IDs (ORD-001, ORD-002, ORD-004, etc.), and next queued orders concisely.
8. When instructed to assign a floater, call assign_floater ONLY for the exact workstation(s) specified by the user. Do NOT assign floaters to other machines or lines.
9. When asked 'who is replaced for absent operators' or 'who replaced whom', clearly identify the relief floaters covering for absent operators (from `active_replacements` or `replacement_employee_name`). Do NOT confuse the absent operators with their replacements.
10. When asked for absent operators across the factory, include all absentees across all production lines in the plant with their workstations and lines.
11. When asked about available Free Pool operators or standby floaters, report ONLY unassigned standby personnel. If an employee is on standby in the Free Pool, do NOT claim they are assigned to a machine. If a floater has already been deployed to a machine (is_replacement=True or machine_id assigned), report them as an active operating floater covering that machine, NOT as available in the Free Pool.
12. When investigating why a line is halted, stopped, or slowed down, check for unstaffed operator bottlenecks first (using get_shortfall_analysis, get_employees_data, or get_machine_status). If an absent operator has no floater assigned to their workstation, conclude definitively that the line is halted due to the UNSTAFFED OPERATOR BOTTLENECK. Do NOT invent or search for mechanical coolant faults when an unstaffed operator bottleneck is present.
13. Never cite or output raw hex event IDs (e.g. 7c417439...). Always cite clean evidence bracket tags like [E1], [E2] or workstation/machine IDs like L01-M03.
14. Give ONLY the direct answer in 1 or 2 clear lines. NEVER output repetitive boilerplate headers like 'Problem Statement:', 'Direct Observed Causes:', 'Operational Guidelines:', or 'Root Cause Determination:'. Answer directly without filler.
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
    mode: str  # 'ollama', 'guardrail_blocked', or 'ollama_error'
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
# 2. Ollama Communication Layer
# =====================================================================

def _is_local_endpoint_alive(host: str = "127.0.0.1", port: int = 11434) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except (OSError, TimeoutError):
        return False


def _call_llm(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Call local Ollama /v1/chat/completions endpoint."""
    base_url = os.getenv("LLM_API_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    if not _is_local_endpoint_alive():
        raise RuntimeError("Local Ollama service (127.0.0.1:11434) is not running.")

    api_key = os.getenv("LLM_API_KEY", "ollama")
    model = os.getenv("LLM_MODEL", "llama3.2")

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

    timeout_sec = int(os.getenv("LLM_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS)))
    try:
        with urlopen(req, timeout=timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        err_msg = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama API returned HTTP {exc.code}: {err_msg}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Ollama network error: {exc}") from exc


def _get_factory_dates(factory_id: str) -> tuple[str, str]:
    fid = normalize_factory_id(factory_id)
    matches = [p for p in DATA_DIR.glob(f"{fid}-*") if p.is_dir()] or [p for p in DATA_DIR.glob(f"{factory_id}-*") if p.is_dir()]
    if not matches:
        t = datetime.now().astimezone().date().isoformat()
        y = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
        return t, y
    available = sorted([p.name for p in matches[0].iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)])
    if not available:
        t = datetime.now().astimezone().date().isoformat()
        y = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
        return t, y
    today_str = available[-1]
    yesterday_str = available[-2] if len(available) >= 2 else (date.fromisoformat(today_str) - timedelta(days=1)).isoformat()
    return today_str, yesterday_str


# =====================================================================
# 3. The Agent Harness Runtime (Ollama Exclusively)
# =====================================================================

class AgentHarness:
    """The runtime orchestrator for autonomous operations investigations using Ollama exclusively."""

    def __init__(self, registry: ToolRegistry | None = None, system_prompt: str | None = None) -> None:
        self.evidence_store = registry.evidence_store if registry else EvidenceStore()
        self.registry = registry or ToolRegistry(self.evidence_store)
        self.system_prompt = system_prompt

    def run(
        self,
        question: str,
        factory_id: str = "F001",
        history: list[dict[str, str]] | None = None,
    ) -> InvestigationResult:
        trace: list[TraceEvent] = []

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

        # Step 3: Verify Ollama Service Availability
        if not _is_local_endpoint_alive():
            err_msg = (
                "### Ollama Service Offline\n\n"
                "The local Ollama service at `http://127.0.0.1:11434` is not running. "
                "FactoryOS is configured to use **Ollama exclusively** (deterministic fallback has been completely removed). "
                "Please start Ollama in your terminal (`ollama serve`) and try again."
            )
            trace.append(TraceEvent(_now(), 0, "ollama_offline", "Ollama endpoint 127.0.0.1:11434 is unreachable."))
            return InvestigationResult(
                question=clean_question,
                factory_id=clean_factory_id,
                mode="ollama_error",
                skill_name=skill_name,
                rounds_executed=0,
                final_answer=err_msg,
                evidence=[],
                trace=trace,
            )

        # Step 4: Ollama ReAct Loop with Skill SOP Injection
        active_system_prompt = self.system_prompt or SYSTEM_PROMPT
        if matched_skill:
            active_system_prompt += matched_skill.format_prompt_injection()

        today_str, yesterday_str = _get_factory_dates(clean_factory_id)
        date_context = f"\nOperational Dates: Today is '{today_str}'. Yesterday is '{yesterday_str}'. If the user asks about yesterday, use day='{yesterday_str}'. If the user asks about today, use day='{today_str}'."
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": active_system_prompt + date_context},
        ]

        # Multi-turn conversational memory injection
        if history:
            for turn in history[-6:]:
                role = str(turn.get("role", "")).lower()
                content = str(turn.get("content", "")).strip()
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        messages.append({
            "role": "user",
            "content": f"Factory ID: {clean_factory_id}\nReference Date (Today): {today_str}\nYesterday: {yesterday_str}\nQuestion: {clean_question}\nPlease investigate using the available read-only tools and cite evidence IDs.",
        })

        allowed_tools = {s["function"]["name"] for s in self.registry.schemas}
        round_index = 0
        final_answer = ""

        while round_index < MAX_TOOL_ROUNDS:
            round_index += 1
            trace.append(TraceEvent(_now(), round_index, "thought", f"Requesting Ollama round {round_index}"))

            try:
                response = _call_llm(messages=messages, tools=self.registry.schemas)
            except RuntimeError as exc:
                err_msg = f"### Ollama Execution Error\n\nFailed to communicate with Ollama: {exc}"
                trace.append(TraceEvent(_now(), round_index, "error", str(exc)))
                return InvestigationResult(
                    question=clean_question,
                    factory_id=clean_factory_id,
                    mode="ollama_error",
                    skill_name=skill_name,
                    rounds_executed=round_index,
                    final_answer=err_msg,
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
            messages.append(message)

            for call in tool_calls:
                func = call.get("function", {})
                tool_name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # Force factory scoping to prevent cross-factory leakage
                args["factory_id"] = clean_factory_id

                # Sanitize arguments: remove hallucinated evidence brackets from parameters
                for k, v in list(args.items()):
                    if isinstance(v, str) and re.search(r"\[E\d+\]", v, re.IGNORECASE):
                        del args[k]

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
                    if isinstance(observation, list) and ev_ids:
                        for idx, item in enumerate(observation):
                            if isinstance(item, dict):
                                item["citation_id"] = f"[{ev_ids[idx]}]" if idx < len(ev_ids) else f"[{ev_ids[0]}]"
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
            mode="ollama",
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
    print("Testing FactoryOS V2 Agent Harness (Ollama-only)...\n")
    harness = AgentHarness()
    sample_query = "Why did production miss target on factory F001? What does the manual procedure say about coolant pump vibration?"
    result = harness.run(question=sample_query, factory_id="F001")
    print(f"Mode: {result.mode}")
    print(f"Rounds executed: {result.rounds_executed}")
    print(f"Final Answer Preview:\n{result.final_answer[:200]}...")
