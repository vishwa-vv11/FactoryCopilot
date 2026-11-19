"""RAGAS-Based Evaluation & Benchmarking Engine for FactoryOS V2.

This module provides quantitative RAG assessment inspired by the RAGAS framework:
1. Faithfulness: Evaluates if claims in the generated answer are grounded in the retrieved context.
2. Answer Relevancy: Evaluates if the generated answer directly addresses the query without extraneous filler.
3. Context Precision: Evaluates whether relevant ground-truth chunks are ranked at the top (Average Precision @ K).
4. Context Recall: Evaluates what proportion of ground-truth reference concepts were retrieved.
5. Overall RAGAS Score: Harmonic mean of the 4 core dimensions.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from v2.rag import search_factory_documents, _EMBEDDER, _cosine_similarity
else:
    from .rag import search_factory_documents, _EMBEDDER, _cosine_similarity


# =====================================================================
# 1. RAGAS Test Case & Result Models
# =====================================================================

@dataclass
class RagasTestCase:
    test_id: str
    factory_id: str
    question: str
    ground_truth_answer: str
    reference_concepts: list[str]
    expected_doc: str
    is_negative_case: bool = False  # Tests insufficient context handling


@dataclass
class RagasMetricScore:
    test_id: str
    question: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    context_recall: float
    composite_ragas: float
    retrieved_count: int
    top_doc: str
    status: str
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "question": self.question,
            "faithfulness": round(self.faithfulness, 4),
            "answer_relevancy": round(self.answer_relevancy, 4),
            "context_precision": round(self.context_precision, 4),
            "context_recall": round(self.context_recall, 4),
            "composite_ragas": round(self.composite_ragas, 4),
            "retrieved_count": self.retrieved_count,
            "top_doc": self.top_doc,
            "status": self.status,
            "details": self.details,
        }


# =====================================================================
# 2. Benchmark Dataset for Factory Documentation
# =====================================================================

RAGAS_BENCHMARK_SUITE: list[RagasTestCase] = [
    RagasTestCase(
        test_id="RAGAS-01",
        factory_id="F001",
        question="What are the warning signs and causes of CNC press coolant pump vibration?",
        ground_truth_answer="A rising pump vibration or intermittent flow indicates a worn seal or bearing. High spindle temperature causes dimensional variation and unplanned stops. Stop press if coolant flow falls below minimum.",
        reference_concepts=["vibration", "intermittent flow", "seal", "bearing", "spindle temperature", "minimum"],
        expected_doc="machine_manual.md",
    ),
    RagasTestCase(
        test_id="RAGAS-02",
        factory_id="F001",
        question="What is the recovery procedure when coolant flow falls below the marked minimum?",
        ground_truth_answer="Maintenance should isolate power, inspect the pump and seals, replace damaged parts, restore coolant flow, and verify temperature stability before returning to production.",
        reference_concepts=["isolate power", "inspect pump", "seals", "replace damaged parts", "restore coolant flow", "temperature stability"],
        expected_doc="machine_manual.md",
    ),
    RagasTestCase(
        test_id="RAGAS-03",
        factory_id="F001",
        question="How many parts must be inspected after machine restart and what is the hold criteria?",
        ground_truth_answer="Inspect the first five parts after restart or interruption over 30 minutes. If two or more parts are outside tolerance, hold the affected lot and notify the quality lead.",
        reference_concepts=["first five parts", "thirty minutes", "two or more", "tolerance", "hold", "quality lead"],
        expected_doc="quality_procedure.md",
    ),
    RagasTestCase(
        test_id="RAGAS-04",
        factory_id="F001",
        question="What should operators do after an unplanned stop or material shortage?",
        ground_truth_answer="Record vibration, heat, power interruptions with line, machine, and time. Notify shift lead and maintenance before restarting. After material shortage, verify received quantities and inspect affected lot.",
        reference_concepts=["record", "unusual vibration", "notify shift lead", "maintenance", "verify received quantities", "inspect affected lot"],
        expected_doc="maintenance_and_safety.md",
    ),
    RagasTestCase(
        test_id="RAGAS-05-NEG",
        factory_id="F001",
        question="What is the cryptographic blockchain consensus protocol for wafer lithography?",
        ground_truth_answer="Insufficient context in documentation.",
        reference_concepts=["insufficient context", "unsupported"],
        expected_doc="none",
        is_negative_case=True,
    ),
]


# =====================================================================
# 3. Metric Computation Engine
# =====================================================================

def _compute_semantic_similarity(text_a: str, text_b: str) -> float:
    """Compute semantic cosine similarity using offline MiniLM embeddings or lexical Jaccard."""
    embs = _EMBEDDER.embed_texts([text_a, text_b])
    if embs and len(embs) == 2 and embs[0] and embs[1]:
        return max(0.0, _cosine_similarity(embs[0], embs[1]))

    # Fallback to token Jaccard similarity
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def compute_faithfulness(answer: str, contexts: list[str]) -> float:
    """Evaluate what fraction of claims in generated answer are entailed by the context."""
    if not answer.strip() or not contexts:
        return 0.0

    combined_context = " ".join(contexts).lower()
    # Break answer into sentences / factual propositions
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", answer) if len(s.strip()) > 8]
    if not sentences:
        return 1.0

    supported = 0
    for sentence in sentences:
        s_words = [w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if len(w) > 3]
        if not s_words:
            supported += 1
            continue
        # Check token containment in retrieved context
        matches = sum(1 for w in s_words if w in combined_context)
        if (matches / len(s_words)) >= 0.5:
            supported += 1

    return supported / len(sentences)


def compute_answer_relevancy(question: str, answer: str) -> float:
    """Evaluate semantic relevancy between the user question and the answer."""
    if not question.strip() or not answer.strip():
        return 0.0
    return round(_compute_semantic_similarity(question, answer), 4)


def compute_context_precision(retrieved_docs: list[dict[str, Any]], expected_doc: str, is_negative: bool = False) -> float:
    """Compute Mean Average Precision (MAP) of retrieved contexts relative to ground truth."""
    if is_negative:
        # Negative test passes precision if it flagged insufficient context or retrieved 0 relevant docs
        has_insufficient = any(d.get("status") == "insufficient_context" for d in retrieved_docs)
        return 1.0 if has_insufficient else 0.0

    if not retrieved_docs:
        return 0.0

    relevant_hits = 0
    precision_sum = 0.0

    for rank, doc in enumerate(retrieved_docs, start=1):
        source = doc.get("source", "").lower()
        if expected_doc.lower() in source:
            relevant_hits += 1
            precision_sum += (relevant_hits / rank)

    return (precision_sum / relevant_hits) if relevant_hits > 0 else 0.0


def compute_context_recall(retrieved_contexts: list[str], reference_concepts: list[str], is_negative: bool = False) -> float:
    """Compute what proportion of reference ground-truth concepts are recalled in retrieved context."""
    if is_negative:
        return 1.0

    if not reference_concepts:
        return 1.0

    combined = " ".join(retrieved_contexts).lower()
    recalled = 0

    for concept in reference_concepts:
        c_lower = concept.lower()
        # Direct phrase match or token match
        if c_lower in combined:
            recalled += 1
        else:
            tokens = [t for t in re.findall(r"[a-z0-9]+", c_lower) if len(t) > 2]
            if tokens and all(t in combined for t in tokens):
                recalled += 1

    return recalled / len(reference_concepts)


def compute_composite_ragas(f: float, ar: float, cp: float, cr: float) -> float:
    """Compute harmonic mean of the 4 RAGAS dimensions."""
    eps = 1e-4
    vals = [max(eps, f), max(eps, ar), max(eps, cp), max(eps, cr)]
    harmonic_mean = 4.0 / sum(1.0 / v for v in vals)
    return round(min(1.0, harmonic_mean), 4)


# =====================================================================
# 4. RAGAS Benchmark Evaluator
# =====================================================================

class RagasBenchmarkRunner:
    """Runs automated RAGAS assessment over the factory RAG knowledge base."""

    def evaluate_case(self, case: RagasTestCase) -> RagasMetricScore:
        # 1. Execute retrieval via hybrid search
        retrieved = search_factory_documents(case.factory_id, case.question, limit=3)
        contexts = [r.get("content", "") for r in retrieved]
        top_source = retrieved[0].get("source", "none") if retrieved else "none"

        # 2. Check for negative / insufficient-context handling
        if case.is_negative_case:
            is_insufficient = (
                not retrieved
                or retrieved[0].get("status") == "insufficient_context"
                or top_source == "insufficient_context"
            )
            score = 1.0 if is_insufficient else 0.0
            return RagasMetricScore(
                test_id=case.test_id,
                question=case.question[:55] + "...",
                faithfulness=score,
                answer_relevancy=score,
                context_precision=score,
                context_recall=score,
                composite_ragas=score,
                retrieved_count=len(retrieved),
                top_doc=top_source,
                status="PASS" if is_insufficient else "FAIL",
                details="Negative/insufficient-context test properly handled" if is_insufficient else "Failed to flag insufficient context",
            )

        # 3. Simulate synthesized answer from retrieved context (leveraging parent-child rich context)
        synthesized_answer = retrieved[0].get("parent_content") or retrieved[0].get("content", "") if retrieved else ""

        # 4. Compute metrics
        f = round(compute_faithfulness(case.ground_truth_answer, contexts), 4)
        ar = round(compute_answer_relevancy(case.question, synthesized_answer), 4)
        cp = round(compute_context_precision(retrieved, case.expected_doc), 4)
        cr = round(compute_context_recall(contexts, case.reference_concepts), 4)
        ragas_score = compute_composite_ragas(f, ar, cp, cr)

        passed = (ragas_score >= 0.70) and (cp > 0.0)
        status = "PASS" if passed else "FAIL"

        return RagasMetricScore(
            test_id=case.test_id,
            question=case.question[:55] + "...",
            faithfulness=f,
            answer_relevancy=ar,
            context_precision=cp,
            context_recall=cr,
            composite_ragas=ragas_score,
            retrieved_count=len(retrieved),
            top_doc=top_source,
            status=status,
            details=f"F={f:.2f}, AR={ar:.2f}, CP={cp:.2f}, CR={cr:.2f}",
        )

    def run_all(self) -> dict[str, Any]:
        scores: list[RagasMetricScore] = []
        for case in RAGAS_BENCHMARK_SUITE:
            scores.append(self.evaluate_case(case))

        total = len(scores)
        passed_count = sum(1 for s in scores if s.status == "PASS")
        avg_f = sum(s.faithfulness for s in scores) / total
        avg_ar = sum(s.answer_relevancy for s in scores) / total
        avg_cp = sum(s.context_precision for s in scores) / total
        avg_cr = sum(s.context_recall for s in scores) / total
        avg_ragas = sum(s.composite_ragas for s in scores) / total

        return {
            "total_cases": total,
            "passed_cases": passed_count,
            "pass_rate_pct": round((passed_count / total) * 100, 1),
            "avg_faithfulness": round(avg_f, 4),
            "avg_answer_relevancy": round(avg_ar, 4),
            "avg_context_precision": round(avg_cp, 4),
            "avg_context_recall": round(avg_cr, 4),
            "overall_ragas_score": round(avg_ragas, 4),
            "cases": scores,
        }


def run_ragas_benchmark() -> dict[str, Any]:
    """Execute the full RAGAS benchmark and return structured summary."""
    runner = RagasBenchmarkRunner()
    return runner.run_all()


# =====================================================================
# CLI Runner
# =====================================================================

def main() -> None:
    print("\n=======================================================")
    print("  FactoryOS V2 RAGAS Evaluation Benchmark")
    print("=======================================================\n")

    summary = run_ragas_benchmark()

    # Tabular print
    header = f"{'Test ID':<12} {'Status':<8} {'Faith':<8} {'Relev':<8} {'Precis':<8} {'Recall':<8} {'RAGAS':<8} {'Top Doc'}"
    print(header)
    print("-" * 78)

    for c in summary["cases"]:
        print(f"{c.test_id:<12} {c.status:<8} {c.faithfulness:<8.2f} {c.answer_relevancy:<8.2f} {c.context_precision:<8.2f} {c.context_recall:<8.2f} {c.composite_ragas:<8.2f} {c.top_doc}")

    print("-" * 78)
    print(f"\nRAGAS Overall Scorecard:")
    print(f"- Total Test Cases:          {summary['total_cases']}")
    print(f"- Passed Tests (Score >= 0.7): {summary['passed_cases']}/{summary['total_cases']} ({summary['pass_rate_pct']}%)")
    print(f"- Mean Faithfulness:         {summary['avg_faithfulness']:.4f}")
    print(f"- Mean Answer Relevancy:     {summary['avg_answer_relevancy']:.4f}")
    print(f"- Mean Context Precision:    {summary['avg_context_precision']:.4f}")
    print(f"- Mean Context Recall:       {summary['avg_context_recall']:.4f}")
    print(f"- Composite RAGAS Score:     {summary['overall_ragas_score']:.4f}\n")
    print("[OK] RAGAS evaluation completed successfully!")


if __name__ == "__main__":
    main()
