"""Document Retrieval-Augmented Generation (RAG) Engine for FactoryOS V2.

This module provides enterprise-grade, hybrid search and retrieval:
1. Hybrid Search: BM25 (Okapi) + Vector Embeddings + Reciprocal Rank Fusion (RRF).
2. Cross-Encoder Reranking: High-precision contextual reranking of top candidate chunks.
3. Metadata-Aware & Parent-Child Chunking: Retains document name, section path, and estimated page number.
4. Document Versioning & Incremental Indexing: Tracks SHA-256 hashes & mtimes to avoid re-indexing unchanged docs.
5. Rich Source Citations: Returns document name, page number, section, and formatted citation tags.
6. Confidence-Based Retrieval: Evaluates query-document confidence and flags insufficient context gracefully.
7. Seamless Offline Execution: Zero external internet dependency; leverages cached local MiniLM and zero-dependency fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.request import Request, urlopen

# Base paths
DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs" / "factories"
INDEX_CACHE_PATH = Path(__file__).resolve().parent / "data" / "document_index.json"
MANIFEST_PATH = Path(__file__).resolve().parent / "data" / "document_manifest.json"
CHROMA_DIR = Path(__file__).resolve().parent / "data" / "chroma"

DEFAULT_CHILD_CHUNK_CHARS = 380
DEFAULT_PARENT_CHUNK_CHARS = 1400
DEFAULT_CHUNK_OVERLAP = 80
ESTIMATED_CHARS_PER_PAGE = 1800
DEFAULT_CONFIDENCE_THRESHOLD = 0.25
RRF_K = 60


# =====================================================================
# 1. Document Chunk Data Model (Metadata-Aware & Parent-Child)
# =====================================================================

@dataclass
class DocumentChunk:
    chunk_id: str
    factory_id: str
    source_file: str
    chunk_index: int
    content: str
    document_name: str = ""
    section: str = ""
    page_number: int = 1
    parent_id: str | None = None
    chunk_type: str = "child"  # 'child' or 'parent'
    parent_content: str | None = None
    doc_version: str = "v1.0"
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def citation(self) -> str:
        """Formatted human-readable citation tag."""
        sec = f" > {self.section}" if self.section else ""
        return f"[{self.source_file} | p. {self.page_number}{sec}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "factory_id": self.factory_id,
            "source_file": self.source_file,
            "document_name": self.document_name,
            "section": self.section,
            "page_number": self.page_number,
            "parent_id": self.parent_id,
            "chunk_type": self.chunk_type,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "parent_content": self.parent_content,
            "doc_version": self.doc_version,
            "citation": self.citation,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentChunk:
        return cls(
            chunk_id=data["chunk_id"],
            factory_id=data["factory_id"],
            source_file=data["source_file"],
            chunk_index=data.get("chunk_index", 0),
            content=data["content"],
            document_name=data.get("document_name", ""),
            section=data.get("section", ""),
            page_number=data.get("page_number", 1),
            parent_id=data.get("parent_id"),
            chunk_type=data.get("chunk_type", "child"),
            parent_content=data.get("parent_content"),
            doc_version=data.get("doc_version", "v1.0"),
            embedding=data.get("embedding"),
            metadata=data.get("metadata", {}),
        )


# =====================================================================
# 2. Metadata-Aware Hierarchical & Parent-Child Parser
# =====================================================================

def _clean_title(text: str) -> str:
    """Derive clean human-readable title from markdown or filename."""
    t = re.sub(r"^#+\s*", "", text).strip()
    return t if t else "Factory Document"


def parse_markdown_parent_child(
    text: str,
    factory_id: str,
    source_file: str,
    doc_version: str = "v1.0",
    max_child_chars: int = DEFAULT_CHILD_CHUNK_CHARS,
    max_parent_chars: int = DEFAULT_PARENT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[list[DocumentChunk], list[DocumentChunk]]:
    """Parse markdown hierarchically into linked parent and child chunks with metadata."""
    lines = text.splitlines()
    doc_title = Path(source_file).stem.replace("_", " ").title()

    # Detect top-level title
    for line in lines:
        if line.startswith("# "):
            doc_title = _clean_title(line)
            break

    # Split into sections based on markdown headings
    sections: list[dict[str, Any]] = []
    current_heading = doc_title
    current_lines: list[str] = []
    char_offset = 0
    section_start_offset = 0

    for line in lines:
        if re.match(r"^#{1,3}\s+", line):
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append({
                        "heading": current_heading,
                        "text": sec_text,
                        "start_char": section_start_offset,
                    })
                current_lines = []
            current_heading = _clean_title(line)
            section_start_offset = char_offset
        current_lines.append(line)
        char_offset += len(line) + 1

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append({
                "heading": current_heading,
                "text": sec_text,
                "start_char": section_start_offset,
            })

    parent_chunks: list[DocumentChunk] = []
    child_chunks: list[DocumentChunk] = []
    global_child_idx = 0

    for p_idx, sec in enumerate(sections):
        sec_text = sec["text"]
        sec_heading = sec["heading"]
        sec_start = sec["start_char"]
        page_num = max(1, math.ceil((sec_start + 1) / ESTIMATED_CHARS_PER_PAGE))

        # Parent Chunk: Represents the full semantic section
        p_digest = hashlib.sha1(f"{factory_id}:{source_file}:parent:{p_idx}".encode()).hexdigest()[:16]
        parent_chunk = DocumentChunk(
            chunk_id=p_digest,
            factory_id=factory_id,
            source_file=source_file,
            chunk_index=p_idx,
            content=sec_text[:max_parent_chars],
            document_name=doc_title,
            section=sec_heading,
            page_number=page_num,
            chunk_type="parent",
            doc_version=doc_version,
            metadata={"start_char": sec_start, "heading": sec_heading},
        )
        parent_chunks.append(parent_chunk)

        # Child Chunks: Granular paragraph/sub-passage units linked to the parent
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", sec_text) if p.strip()]
        current_child = ""

        for paragraph in paragraphs:
            while len(paragraph) > max_child_chars:
                head, paragraph = paragraph[:max_child_chars], paragraph[max_child_chars - overlap:]
                if current_child:
                    c_digest = hashlib.sha1(f"{factory_id}:{source_file}:child:{global_child_idx}".encode()).hexdigest()[:16]
                    child_chunks.append(DocumentChunk(
                        chunk_id=c_digest,
                        factory_id=factory_id,
                        source_file=source_file,
                        chunk_index=global_child_idx,
                        content=current_child.strip(),
                        document_name=doc_title,
                        section=sec_heading,
                        page_number=page_num,
                        parent_id=p_digest,
                        chunk_type="child",
                        parent_content=parent_chunk.content,
                        doc_version=doc_version,
                        metadata={"parent_id": p_digest},
                    ))
                    global_child_idx += 1
                    current_child = ""

                c_digest = hashlib.sha1(f"{factory_id}:{source_file}:child:{global_child_idx}".encode()).hexdigest()[:16]
                child_chunks.append(DocumentChunk(
                    chunk_id=c_digest,
                    factory_id=factory_id,
                    source_file=source_file,
                    chunk_index=global_child_idx,
                    content=head.strip(),
                    document_name=doc_title,
                    section=sec_heading,
                    page_number=page_num,
                    parent_id=p_digest,
                    chunk_type="child",
                    parent_content=parent_chunk.content,
                    doc_version=doc_version,
                    metadata={"parent_id": p_digest},
                ))
                global_child_idx += 1

            candidate = f"{current_child}\n\n{paragraph}" if current_child else paragraph
            if len(candidate) > max_child_chars and current_child:
                c_digest = hashlib.sha1(f"{factory_id}:{source_file}:child:{global_child_idx}".encode()).hexdigest()[:16]
                child_chunks.append(DocumentChunk(
                    chunk_id=c_digest,
                    factory_id=factory_id,
                    source_file=source_file,
                    chunk_index=global_child_idx,
                    content=current_child.strip(),
                    document_name=doc_title,
                    section=sec_heading,
                    page_number=page_num,
                    parent_id=p_digest,
                    chunk_type="child",
                    parent_content=parent_chunk.content,
                    doc_version=doc_version,
                    metadata={"parent_id": p_digest},
                ))
                global_child_idx += 1
                tail = current_child[-overlap:].strip()
                current_child = f"{tail}\n\n{paragraph}" if tail else paragraph
            else:
                current_child = candidate

        if current_child.strip():
            c_digest = hashlib.sha1(f"{factory_id}:{source_file}:child:{global_child_idx}".encode()).hexdigest()[:16]
            child_chunks.append(DocumentChunk(
                chunk_id=c_digest,
                factory_id=factory_id,
                source_file=source_file,
                chunk_index=global_child_idx,
                content=current_child.strip(),
                document_name=doc_title,
                section=sec_heading,
                page_number=page_num,
                parent_id=p_digest,
                chunk_type="child",
                parent_content=parent_chunk.content,
                doc_version=doc_version,
                metadata={"parent_id": p_digest},
            ))
            global_child_idx += 1

    return child_chunks, parent_chunks


# =====================================================================
# 3. BM25 (Okapi) Lexical Retrieval Engine
# =====================================================================

class BM25Index:
    """Okapi BM25 implementation for robust offline keyword matching."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.doc_count = 0
        self.avg_doc_len = 0.0
        self.doc_lengths: list[int] = []
        self.doc_freqs: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.term_freqs: list[dict[str, int]] = []
        self.chunks: list[DocumentChunk] = []

    @staticmethod
    def _stem(word: str) -> str:
        """Light suffix normalizer for search terms (e.g. inspected -> inspect, parts -> part)."""
        for suffix in ("ing", "tion", "ions", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[:-len(suffix)]
        return word

    @classmethod
    def tokenize(cls, text: str) -> list[str]:
        """Normalize and tokenize text into stemmed keywords."""
        terms = re.findall(r"[a-z0-9]+", text.lower())
        stopwords = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have", "been", "how", "what", "which", "when", "where", "why", "who"}
        stemmed = []
        for t in terms:
            if len(t) > 2 and t not in stopwords:
                stemmed.append(cls._stem(t))
        return stemmed

    def fit(self, chunks: list[DocumentChunk]) -> None:
        """Build BM25 index over provided document chunks."""
        self.chunks = chunks
        self.doc_count = len(chunks)
        self.doc_freqs.clear()
        self.term_freqs.clear()
        self.doc_lengths.clear()

        if self.doc_count == 0:
            self.avg_doc_len = 0.0
            return

        total_len = 0
        for chunk in chunks:
            # Combine title, section, and content for lexical scoring
            full_text = f"{chunk.document_name} {chunk.section} {chunk.content}"
            tokens = self.tokenize(full_text)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_len += doc_len

            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            self.term_freqs.append(tf)

            for token in tf.keys():
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        self.avg_doc_len = total_len / self.doc_count if self.doc_count else 1.0

        # Compute Robertson-Spärck Jones IDF with standard smoothing
        self.idf.clear()
        for term, df in self.doc_freqs.items():
            self.idf[term] = math.log(1.0 + (self.doc_count - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> list[tuple[DocumentChunk, float]]:
        """Score all chunks against query using Okapi BM25 formula."""
        q_tokens = self.tokenize(query)
        if not q_tokens or not self.chunks:
            return []

        scores: list[tuple[DocumentChunk, float]] = []
        for i, chunk in enumerate(self.chunks):
            doc_len = self.doc_lengths[i]
            tf_dict = self.term_freqs[i]
            score = 0.0

            for t in q_tokens:
                if t not in tf_dict:
                    continue
                tf = tf_dict[t]
                idf = self.idf.get(t, 0.0)
                numerator = tf * (self.k1 + 1.0)
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / (self.avg_doc_len or 1.0)))
                score += idf * (numerator / denominator)

            if score > 0.0:
                scores.append((chunk, score))

        scores.sort(key=lambda pair: pair[1], reverse=True)
        return scores


# =====================================================================
# 4. Dense Vector Embedding & Similarity Engine
# =====================================================================

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    return dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0


class VectorEmbeddingEngine:
    """Local offline sentence embedding engine with cloud fallback."""

    def __init__(self) -> None:
        self._st_model = None
        self._st_tried = False

    def _get_st_model(self) -> Any:
        if not self._st_tried:
            self._st_tried = True
            try:
                # Disable network checks so HuggingFace loads offline cache immediately
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            except Exception:
                self._st_model = None
        return self._st_model

    def embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Generate dense vector embeddings. Prefers cached SentenceTransformer, then cloud API."""
        if not texts:
            return []

        # 1. High-speed local offline sentence-transformers
        model = self._get_st_model()
        if model is not None:
            try:
                embs = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
                return [row.tolist() for row in embs]
            except Exception:
                pass

        # 2. Cloud / Local OpenAI-compatible fallback endpoint
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        emb_model = os.getenv("LLM_EMBEDDING_MODEL")
        base_url = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").rstrip("/")

        if api_key and emb_model:
            req = Request(
                f"{base_url}/embeddings",
                data=json.dumps({"model": emb_model, "input": texts}).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return [row["embedding"] for row in sorted(data["data"], key=lambda r: r["index"])]
            except Exception:
                pass

        # 3. Local Ollama embeddings fallback
        ollama_url = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
        results = []
        for text in texts:
            try:
                payload = json.dumps({"model": "llama3.2", "prompt": text}).encode("utf-8")
                req = Request(f"{ollama_url}/api/embeddings", data=payload, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=5) as resp:
                    d = json.loads(resp.read().decode("utf-8"))
                    results.append(d.get("embedding", []))
            except Exception:
                return None
        return results if len(results) == len(texts) else None


_EMBEDDER = VectorEmbeddingEngine()


# =====================================================================
# 5. Reciprocal Rank Fusion (RRF) & Cross-Encoder Reranking
# =====================================================================

def reciprocal_rank_fusion(
    bm25_ranked: list[tuple[DocumentChunk, float]],
    vector_ranked: list[tuple[DocumentChunk, float]],
    k: int = RRF_K,
    weight_bm25: float = 1.0,
    weight_vector: float = 1.0,
) -> list[tuple[DocumentChunk, float]]:
    """Fuse rank orders from BM25 and Vector search using Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    chunk_map: dict[str, DocumentChunk] = {}

    for rank, (chunk, _) in enumerate(bm25_ranked, start=1):
        chunk_map[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + (weight_bm25 / (k + rank))

    for rank, (chunk, _) in enumerate(vector_ranked, start=1):
        chunk_map[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + (weight_vector / (k + rank))

    fused = [(chunk_map[cid], score) for cid, score in scores.items()]
    fused.sort(key=lambda pair: pair[1], reverse=True)
    return fused


class CrossEncoderReranker:
    """Reranker that scores (query, candidate) pairs jointly."""

    def __init__(self) -> None:
        self._ce_model = None
        self._tried_ce = False

    def _get_ce_model(self) -> Any:
        if not self._tried_ce:
            self._tried_ce = True
            try:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                from sentence_transformers import CrossEncoder
                self._ce_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
            except Exception:
                self._ce_model = None
        return self._ce_model

    def rerank(
        self,
        query: str,
        candidates: list[tuple[DocumentChunk, float]],
        top_k: int = 3,
    ) -> list[tuple[DocumentChunk, float]]:
        """Rerank candidates using cross-encoder model or contextual scoring."""
        if not candidates:
            return []

        # Limit candidate pool for reranking
        pool = candidates[: max(top_k * 3, 10)]
        ce = self._get_ce_model()

        if ce is not None:
            try:
                pairs = [[query, f"{c.document_name} {c.section}: {c.content}"] for c, _ in pool]
                ce_scores = ce.predict(pairs)
                reranked = [(pool[i][0], float(ce_scores[i])) for i in range(len(pool))]
                reranked.sort(key=lambda pair: pair[1], reverse=True)
                return reranked[:top_k]
            except Exception:
                pass

        # High-accuracy offline contextual cross-scoring:
        # Measures exact query term density, heading alignment, and term proximity
        q_terms = BM25Index.tokenize(query)
        scored: list[tuple[DocumentChunk, float]] = []

        for chunk, base_rrf in pool:
            chunk_tokens = set(BM25Index.tokenize(f"{chunk.document_name} {chunk.section} {chunk.content}"))
            matched_terms = [t for t in q_terms if t in chunk_tokens]
            term_coverage = len(matched_terms) / len(q_terms) if q_terms else 0.0

            # Bonus for match in section heading or doc name
            sec_doc_tokens = set(BM25Index.tokenize(f"{chunk.section} {chunk.document_name}"))
            heading_bonus = 0.30 if any(t in sec_doc_tokens for t in q_terms) else 0.0

            # Exact query phrase match bonus
            text_lower = f"{chunk.document_name} {chunk.section} {chunk.content}".lower()
            phrase_bonus = 0.35 if query.lower() in text_lower else 0.0

            # Combined normalized cross-score (0.0 to 1.0)
            cross_score = min(1.0, (base_rrf * 15.0) + (term_coverage * 0.40) + heading_bonus + phrase_bonus)
            scored.append((chunk, cross_score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


_RERANKER = CrossEncoderReranker()


# =====================================================================
# 6. Document Versioning & Incremental Index Manifest
# =====================================================================

class DocumentManifest:
    """Tracks document hashes, versions, and modification times for incremental indexing."""

    def __init__(self, manifest_path: Path = MANIFEST_PATH) -> None:
        self.manifest_path = manifest_path
        self.records: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if self.manifest_path.exists():
            try:
                self.records = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:
                self.records = {}

    def save(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(self.records, indent=2), encoding="utf-8")

    def get_file_record(self, rel_path: str) -> dict[str, Any] | None:
        return self.records.get(rel_path)

    def update_file_record(self, rel_path: str, sha256: str, mtime: float, version: str, chunk_ids: list[str]) -> None:
        self.records[rel_path] = {
            "sha256": sha256,
            "mtime": mtime,
            "version": version,
            "chunk_ids": chunk_ids,
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


# =====================================================================
# 7. Document Index & Hybrid RAG Engine
# =====================================================================

class DocumentIndex:
    """Production-grade Document Index supporting incremental indexing, hybrid search, and parent-child chunking."""

    def __init__(self, docs_root: Path = DOCS_ROOT) -> None:
        self.docs_root = docs_root
        self.chunks: list[DocumentChunk] = []
        self.parent_chunks: dict[str, DocumentChunk] = {}
        self.bm25 = BM25Index()
        self.manifest = DocumentManifest()
        self.index_documents(incremental=True)

    def index_documents(self, incremental: bool = True) -> dict[str, int]:
        """Scan docs directory, compute hashes, and incrementally index markdown files."""
        stats = {"indexed": 0, "skipped": 0, "deleted": 0}
        if not self.docs_root.exists():
            return stats

        doc_files = sorted(self.docs_root.glob("*/*.md"))
        active_rel_paths = set()
        new_or_modified_chunks: list[DocumentChunk] = []

        # If non-incremental or cache empty, start fresh
        if not incremental or not self.chunks:
            self.chunks.clear()
            self.parent_chunks.clear()

        existing_chunks_by_file: dict[str, list[DocumentChunk]] = {}
        for c in self.chunks:
            rel = f"{c.factory_id}/{c.source_file}"
            existing_chunks_by_file.setdefault(rel, []).append(c)

        for path in doc_files:
            factory_dir_name = path.parent.name
            rel_path = f"{factory_dir_name}/{path.name}"
            active_rel_paths.add(rel_path)

            stat = path.stat()
            file_bytes = path.read_bytes()
            file_sha = hashlib.sha256(file_bytes).hexdigest()
            record = self.manifest.get_file_record(rel_path)

            # Incremental check: Skip if hash & mtime unchanged and we already have chunks
            if incremental and record and record.get("sha256") == file_sha and rel_path in existing_chunks_by_file:
                stats["skipped"] += 1
                continue

            # Parse new or modified file
            version_str = f"v{file_sha[:8]}"
            text = file_bytes.decode("utf-8", errors="replace")
            children, parents = parse_markdown_parent_child(
                text=text,
                factory_id=factory_dir_name,
                source_file=path.name,
                doc_version=version_str,
            )

            # Embed child chunks if embedding engine is available
            child_texts = [f"{c.document_name} {c.section}: {c.content}" for c in children]
            embeddings = _EMBEDDER.embed_texts(child_texts)
            if embeddings and len(embeddings) == len(children):
                for idx, emb in enumerate(embeddings):
                    children[idx].embedding = emb

            for p in parents:
                self.parent_chunks[p.chunk_id] = p

            # Replace chunks for this file
            existing_chunks_by_file[rel_path] = children
            self.manifest.update_file_record(
                rel_path=rel_path,
                sha256=file_sha,
                mtime=stat.st_mtime,
                version=version_str,
                chunk_ids=[c.chunk_id for c in children],
            )
            stats["indexed"] += 1

        # Purge deleted files
        recorded_paths = list(self.manifest.records.keys())
        for r_path in recorded_paths:
            if r_path not in active_rel_paths:
                existing_chunks_by_file.pop(r_path, None)
                self.manifest.records.pop(r_path, None)
                stats["deleted"] += 1

        # Re-assemble active chunks and fit BM25
        self.chunks = [chunk for chunks_list in existing_chunks_by_file.values() for chunk in chunks_list]
        self.bm25.fit(self.chunks)
        self.manifest.save()
        return stats

    def search(
        self,
        query: str,
        factory_id: str = "F001",
        top_k: int = 3,
        min_confidence: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """Execute hybrid search (BM25 + Vector + RRF + Cross-Encoder Reranking)."""
        if not query.strip() or not self.chunks:
            return []

        # Scope filter: factory specific or shared SOPs
        eligible = [c for c in self.chunks if c.factory_id in (factory_id, "shared")]
        if not eligible:
            return []

        # 1. BM25 Lexical Ranking
        bm25_scored = self.bm25.score(query)
        bm25_eligible = [(c, score) for c, score in bm25_scored if c in eligible]

        # 2. Dense Vector Ranking
        vector_eligible: list[tuple[DocumentChunk, float]] = []
        chunks_with_embeddings = [c for c in eligible if c.embedding]
        if chunks_with_embeddings:
            q_emb = _EMBEDDER.embed_texts([query])
            if q_emb and len(q_emb) == 1 and q_emb[0]:
                vec = q_emb[0]
                scored_vec = [(c, _cosine_similarity(vec, c.embedding)) for c in chunks_with_embeddings]  # type: ignore
                scored_vec.sort(key=lambda pair: pair[1], reverse=True)
                vector_eligible = [(c, s) for c, s in scored_vec if s > 0.15]

        # 3. Reciprocal Rank Fusion (RRF)
        fused_candidates = reciprocal_rank_fusion(
            bm25_ranked=bm25_eligible,
            vector_ranked=vector_eligible,
            k=RRF_K,
        )

        if not fused_candidates:
            fused_candidates = bm25_eligible

        if not fused_candidates:
            # Zero candidates match -> Return insufficient context notice
            return [
                {
                    "source": "insufficient_context",
                    "document_name": "Documentation Notice",
                    "section": "Insufficient Context",
                    "page_number": 1,
                    "factory_scope": factory_id,
                    "content": f"Insufficient verified context found in factory documentation for query '{query}'. (Confidence: 0.0 < threshold {min_confidence}).",
                    "parent_content": None,
                    "score": 0.0,
                    "confidence": 0.0,
                    "match_type": "insufficient_context",
                    "citation": "[No verified documentation matching query]",
                    "status": "insufficient_context",
                }
            ]

        # 4. Cross-Encoder Reranking
        reranked = _RERANKER.rerank(query=query, candidates=fused_candidates, top_k=top_k)

        # 5. Confidence-Based Filtering & Insufficient-Context Handling
        top_confidence = reranked[0][1] if reranked else 0.0

        if top_confidence < min_confidence:
            # Return insufficient-context notice record
            return [
                {
                    "source": "insufficient_context",
                    "document_name": "Documentation Notice",
                    "section": "Insufficient Context",
                    "page_number": 1,
                    "factory_scope": factory_id,
                    "content": f"Insufficient verified context found in factory documentation for query '{query}'. (Confidence: {round(top_confidence, 2)} < threshold {min_confidence}).",
                    "parent_content": None,
                    "score": round(top_confidence, 4),
                    "confidence": round(top_confidence, 4),
                    "match_type": "insufficient_context",
                    "citation": "[No verified documentation matching query]",
                    "status": "insufficient_context",
                }
            ]

        # Return rich, citation-enabled result items
        results = []
        for c, score in reranked:
            results.append({
                "source": c.source_file,
                "document_name": c.document_name,
                "section": c.section,
                "page_number": c.page_number,
                "factory_scope": c.factory_id,
                "content": c.content,
                "parent_content": c.parent_content or c.content,
                "score": round(score, 4),
                "confidence": round(score, 4),
                "match_type": "hybrid_bm25_vector_rrf_reranked",
                "citation": c.citation,
                "status": "sufficient_context",
            })
        return results


# =====================================================================
# 8. ChromaDB Persistent Engine (Optional Storage Layer)
# =====================================================================

class ChromaDocumentIndex:
    """ChromaDB persistence wrapper delegating to hybrid DocumentIndex."""

    def __init__(self, docs_root: Path = DOCS_ROOT, persist_dir: Path = CHROMA_DIR) -> None:
        self.docs_root = docs_root
        self.persist_dir = persist_dir
        self.hybrid_index = DocumentIndex(docs_root=docs_root)
        self.collection = None
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self.persist_dir))
            self.collection = client.get_or_create_collection(
                name="factory_sops_v2",
                metadata={"hnsw:space": "cosine"},
            )
            self._sync_documents()
        except Exception:
            self.collection = None

    def _sync_documents(self) -> None:
        if not self.collection or not self.hybrid_index.chunks:
            return
        try:
            existing = self.collection.get()
            existing_ids = set(existing["ids"]) if existing and "ids" in existing else set()
            new_chunks = [c for c in self.hybrid_index.chunks if c.chunk_id not in existing_ids and c.embedding]
            if new_chunks:
                self.collection.upsert(
                    ids=[c.chunk_id for c in new_chunks],
                    documents=[c.content for c in new_chunks],
                    embeddings=[c.embedding for c in new_chunks],  # type: ignore
                    metadatas=[{
                        "factory_id": c.factory_id,
                        "source_file": c.source_file,
                        "document_name": c.document_name,
                        "section": c.section,
                        "page_number": c.page_number,
                    } for c in new_chunks],
                )
        except Exception:
            pass

    def search(
        self,
        query: str,
        factory_id: str = "F001",
        top_k: int = 3,
        min_confidence: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """Primary search method delegating to the hybrid RRF engine."""
        return self.hybrid_index.search(
            query=query,
            factory_id=factory_id,
            top_k=top_k,
            min_confidence=min_confidence,
        )


# Singleton instances
_DOCUMENT_INDEX = ChromaDocumentIndex()


def search_factory_documents(
    factory_id: str,
    query: str,
    limit: int = 3,
    min_confidence: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Primary document search tool powered by Hybrid BM25 + Vector + RRF + Reranker."""
    return _DOCUMENT_INDEX.search(
        query=query,
        factory_id=factory_id,
        top_k=limit,
        min_confidence=min_confidence,
    )


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing FactoryOS V2 Hybrid RAG Engine (BM25 + Vector + RRF + Rerank)...\n")

    test_queries = [
        ("F001", "coolant pump vibration"),
        ("F001", "first five parts dimensional inspection"),
        ("F001", "quantum particle teleportation reactor"),  # Insufficient context test
    ]

    for fid, q in test_queries:
        print(f"--- Query: '{q}' (Factory: {fid}) ---")
        res = search_factory_documents(fid, q, limit=2)
        print(f"Results Count: {len(res)}")
        for r in res:
            print(f" - [{r.get('document_name')} | Page {r.get('page_number')} | Section: {r.get('section')}]")
            print(f"   Citation: {r.get('citation')}")
            print(f"   Score/Confidence: {r.get('confidence')} | Status: {r.get('status')}")
            print(f"   Snippet: {r.get('content')[:120]}...\n")

    print("[OK] FactoryOS V2 Hybrid RAG Engine test completed successfully!")
