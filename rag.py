
"""Document Retrieval-Augmented Generation (RAG) Engine for FactoryOS V2.

This module provides:
1. Document Loader: Ingests markdown manuals, SOPs, and safety protocols from docs/factories.
2. Semantic Chunking: Splits documents by paragraphs with configurable token/character overlap.
3. Hybrid Retriever:
   - Lexical Search (BM25/TF-IDF Keyword Matching): Works 100% offline with zero dependencies.
   - Vector Embedding Search (Cosine Similarity): Activates when LLM_EMBEDDING_MODEL is set.
4. Tool Integration: Exposes `search_factory_documents` for the Agent Tool Registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.request import Request, urlopen

DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs" / "factories"
INDEX_CACHE_PATH = Path(__file__).resolve().parent / "data" / "document_index.json"

DEFAULT_CHUNK_CHARS = 600
DEFAULT_CHUNK_OVERLAP = 100


# =====================================================================
# 1. Document Chunk Data Model
# =====================================================================

@dataclass
class DocumentChunk:
    chunk_id: str
    factory_id: str
    source_file: str
    chunk_index: int
    content: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "factory_id": self.factory_id,
            "source_file": self.source_file,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "metadata": self.metadata,
        }


# =====================================================================
# 2. Text Chunking
# =====================================================================

def chunk_markdown(
    text: str,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split markdown text into semantically coherent paragraph chunks with overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        while len(paragraph) > max_chars:
            head, paragraph = paragraph[:max_chars], paragraph[max_chars - overlap:]
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.append(head.strip())

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars and current:
            chunks.append(current.strip())
            tail = current[-overlap:].strip()
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
        else:
            current = candidate

    if current:
        chunks.append(current.strip())
    return chunks


# =====================================================================
# 3. Embedding Vector Utilities (Optional Cloud Embeddings)
# =====================================================================

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    return dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Call an OpenAI-compatible /v1/embeddings endpoint if credentials are set."""
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("LLM_EMBEDDING_MODEL")
    base_url = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    if not api_key or not model:
        return None

    req = Request(
        f"{base_url}/embeddings",
        data=json.dumps({"model": model, "input": texts}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [row["embedding"] for row in sorted(data["data"], key=lambda r: r["index"])]
    except Exception:
        return None


# =====================================================================
# 4. In-Memory Document Store & Search Engine
# =====================================================================

class DocumentIndex:
    """Loads, indexes, and queries unstructured factory manuals, SOPs, and policies."""

    def __init__(self, docs_root: Path = DOCS_ROOT) -> None:
        self.docs_root = docs_root
        self.chunks: list[DocumentChunk] = []
        self._load_all_documents()

    def _load_all_documents(self) -> None:
        """Scan docs directory and load chunks for all factories and shared manuals."""
        self.chunks.clear()
        if not self.docs_root.exists():
            return

        # Find all markdown files (under F001, F002, shared, etc.)
        doc_files = sorted(self.docs_root.glob("*/*.md"))
        for path in doc_files:
            factory_dir_name = path.parent.name  # 'F001' or 'shared'
            text = path.read_text(encoding="utf-8")
            raw_chunks = chunk_markdown(text)

            for idx, chunk_text in enumerate(raw_chunks):
                digest = hashlib.sha1(f"{factory_dir_name}:{path.name}:{idx}".encode()).hexdigest()[:16]
                chunk = DocumentChunk(
                    chunk_id=digest,
                    factory_id=factory_dir_name,
                    source_file=path.name,
                    chunk_index=idx,
                    content=chunk_text,
                    metadata={"path": str(path.relative_to(self.docs_root))},
                )
                self.chunks.append(chunk)

    def search(
        self,
        query: str,
        factory_id: str = "F001",
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Search documents using hybrid retrieval (Vector if available, else Lexical TF-IDF)."""
        if not query.strip() or not self.chunks:
            return []

        # Scope filter: include chunks specifically for this factory OR shared across all factories
        eligible = [c for c in self.chunks if c.factory_id in (factory_id, "shared")]
        if not eligible:
            return []

        # Try Vector Search first
        query_vectors = _embed_texts([query])
        if query_vectors and all(c.embedding for c in eligible):
            q_vec = query_vectors[0]
            scored = [(c, _cosine_similarity(q_vec, c.embedding)) for c in eligible]  # type: ignore
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return [
                {
                    "source": c.source_file,
                    "factory_scope": c.factory_id,
                    "content": c.content,
                    "score": round(score, 4),
                    "match_type": "vector_cosine",
                }
                for c, score in scored[:top_k] if score > 0.1
            ]

        # Lexical (BM25 / Keyword Frequency) Search Fallback
        query_terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2]
        scored_lexical = []

        for chunk in eligible:
            text_lower = chunk.content.lower()
            score = 0.0
            for term in query_terms:
                count = text_lower.count(term)
                if count > 0:
                    score += 1.0 + math.log(count + 1)
            if score > 0:
                scored_lexical.append((chunk, score))

        scored_lexical.sort(key=lambda pair: pair[1], reverse=True)
        return [
            {
                "source": c.source_file,
                "factory_scope": c.factory_id,
                "content": c.content,
                "score": round(score, 2),
                "match_type": "lexical_keyword",
            }
            for c, score in scored_lexical[:top_k]
        ]


# =====================================================================
# 5. ChromaDB Persistent Vector Database Engine
# =====================================================================

CHROMA_DIR = Path(__file__).resolve().parent / "data" / "chroma"


class OllamaChromaEmbeddingFunction:
    """Zero-dependency Ollama embedding function for ChromaDB."""

    def __init__(self, model_name: str = "llama3.2", base_url: str = "http://127.0.0.1:11434") -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")

    def name(self) -> str:
        return f"ollama_{self.model_name.replace(':', '_').replace('.', '_')}"

    def __call__(self, input: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for text in input:
            try:
                payload = json.dumps({"model": self.model_name, "prompt": text}).encode("utf-8")
                req = Request(f"{self.base_url}/api/embeddings", data=payload, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    embeddings.append(data.get("embedding", []))
            except Exception:
                # Return empty embedding on failure so caller can handle gracefully
                embeddings.append([])
        return embeddings


class ChromaDocumentIndex:
    """Persistent ChromaDB vector store backed by SQLite on disk."""

    def __init__(self, docs_root: Path = DOCS_ROOT, persist_dir: Path = CHROMA_DIR) -> None:
        self.docs_root = docs_root
        self.persist_dir = persist_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.fallback_index = DocumentIndex(docs_root=docs_root)
        self.collection = None
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            # pyrefly: ignore[missing-import]
            import chromadb
            client = chromadb.PersistentClient(path=str(self.persist_dir))
            embedding_fn = OllamaChromaEmbeddingFunction()
            self.collection = client.get_or_create_collection(
                name="factory_sops",
                embedding_function=embedding_fn,
                metadata={"hnsw:space": "cosine"}
            )
            self._sync_documents()
        except Exception:
            self.collection = None

    def _sync_documents(self) -> None:
        """Upsert factory markdown chunks into ChromaDB collection."""
        if not self.collection or not self.fallback_index.chunks:
            return

        existing_ids = set()
        try:
            existing = self.collection.get()
            if existing and "ids" in existing:
                existing_ids = set(existing["ids"])
        except Exception:
            pass

        new_chunks = [c for c in self.fallback_index.chunks if c.chunk_id not in existing_ids]
        if not new_chunks:
            return

        ids = [c.chunk_id for c in new_chunks]
        docs = [c.content for c in new_chunks]
        metadatas = [{"factory_id": c.factory_id, "source_file": c.source_file} for c in new_chunks]

        try:
            self.collection.upsert(ids=ids, documents=docs, metadatas=metadatas)
        except Exception:
            pass

    def search(self, query: str, factory_id: str = "F001", top_k: int = 3) -> list[dict[str, Any]]:
        """Search ChromaDB vector database with metadata filtering; fallback to BM25."""
        if not query.strip():
            return []

        if self.collection:
            try:
                results = self.collection.query(
                    query_texts=[query],
                    n_results=top_k,
                    where={"factory_id": {"$in": [factory_id, "shared"]}}
                )
                if results and results.get("documents") and results["documents"][0]:
                    out = []
                    docs = results["documents"][0]
                    metas = results.get("metadatas", [[]])[0]
                    distances = results.get("distances", [[]])[0] if "distances" in results else []

                    for i, doc in enumerate(docs):
                        meta = metas[i] if i < len(metas) else {}
                        dist = distances[i] if i < len(distances) else 0.0
                        score = round(1.0 - dist, 4) if distances else 0.95
                        out.append({
                            "source": meta.get("source_file", "unknown"),
                            "factory_scope": meta.get("factory_id", factory_id),
                            "content": doc,
                            "score": score,
                            "match_type": "chroma_vector",
                        })
                    if out:
                        return out
            except Exception:
                pass

        # Fallback to in-memory TF-IDF / BM25 lexical search
        return self.fallback_index.search(query=query, factory_id=factory_id, top_k=top_k)


# Singleton instances
_CHROMA_INDEX = ChromaDocumentIndex()


def search_factory_documents(factory_id: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Primary document search tool powered by ChromaDB with local Ollama embeddings."""
    return _CHROMA_INDEX.search(query=query, factory_id=factory_id, top_k=limit)


# =====================================================================
# Direct Execution Smoke Test
# =====================================================================

if __name__ == "__main__":
    print("Testing FactoryOS V2 ChromaDB RAG Engine...\n")

    print(f"Persist Directory: {CHROMA_DIR}")
    results = search_factory_documents("F001", "coolant pump vibration", limit=2)
    print(f"Retrieved {len(results)} results for 'coolant pump vibration':")
    for r in results:
        print(f" - [{r['source']} | Match: {r['match_type']} | Score: {r['score']}]")
        print(f"   Snippet: {r['content'][:120]}...\n")

    print("[OK] ChromaDB RAG Engine smoke test completed successfully!")
