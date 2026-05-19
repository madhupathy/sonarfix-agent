"""SQLite-backed RAG store for past fixes and coding standards.

Stores fix examples as (rule_key, language, before_snippet, after_snippet, embedding)
and retrieves similar examples using cosine similarity on pseudo-embeddings.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DB_PATH = Path.home() / ".sonarfix" / "rag.db"
EMBEDDING_DIM = 64  # dimension for pseudo-embeddings (fallback only)


class EmbeddingModel:
    """Wraps sentence-transformers for real semantic embeddings with trigram fallback."""

    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._model = SentenceTransformer("all-MiniLM-L6-v2")  # small, fast
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def encode(self, text: str) -> List[float]:
        if self._available:
            return self._model.encode(text).tolist()
        return _pseudo_embed(text)  # fallback to trigram hashing


# Module-level singleton so the model is only loaded once per process
_embedding_model: Optional[EmbeddingModel] = None


def _get_embedding_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model


@dataclass
class FixExample:
    """A stored fix example for RAG retrieval."""
    id: int
    rule_key: str
    language: str
    severity: str
    issue_message: str
    before_snippet: str
    after_snippet: str
    explanation: str
    score: float = 0.0  # similarity score when retrieved


@dataclass
class StandardDoc:
    """A stored coding standard/documentation chunk."""
    id: int
    source: str  # e.g. "sonarqube-rules", "project-standards"
    title: str
    content: str
    language: str
    score: float = 0.0


def _pseudo_embed(text: str) -> List[float]:
    """Generate a deterministic pseudo-embedding from text.

    Uses character n-gram hashing to produce a fixed-size vector.
    Not as good as real embeddings but works offline without an API call.
    """
    vec = [0.0] * EMBEDDING_DIM
    text_lower = text.lower()

    # Character trigram hashing
    for i in range(len(text_lower) - 2):
        trigram = text_lower[i:i + 3]
        h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
        idx = h % EMBEDDING_DIM
        vec[idx] += 1.0

    # Word unigram hashing
    for word in text_lower.split():
        h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        idx = h % EMBEDDING_DIM
        vec[idx] += 2.0

    # Normalize
    magnitude = math.sqrt(sum(v * v for v in vec))
    if magnitude > 0:
        vec = [v / magnitude for v in vec]

    return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _serialize_embedding(vec: List[float]) -> bytes:
    """Serialize float vector to bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_embedding(data: bytes) -> List[float]:
    """Deserialize bytes to float vector."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


class RAGStore:
    """SQLite-backed store for fix examples and coding standards."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS fix_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_key TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT '',
                issue_message TEXT NOT NULL DEFAULT '',
                before_snippet TEXT NOT NULL,
                after_snippet TEXT NOT NULL,
                explanation TEXT NOT NULL DEFAULT '',
                embedding BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(rule_key, before_snippet)
            );

            CREATE TABLE IF NOT EXISTS standard_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT '',
                embedding BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, title)
            );

            CREATE INDEX IF NOT EXISTS idx_fix_rule ON fix_examples(rule_key);
            CREATE INDEX IF NOT EXISTS idx_fix_lang ON fix_examples(language);
            CREATE INDEX IF NOT EXISTS idx_std_source ON standard_docs(source);
        """)
        self.conn.commit()

    def store_fix(
        self,
        rule_key: str,
        language: str,
        severity: str,
        issue_message: str,
        before_snippet: str,
        after_snippet: str,
        explanation: str = "",
    ) -> int:
        """Store a successful fix example for future retrieval."""
        # Build embedding from the combination of rule + message + code
        embed_text = f"{rule_key} {issue_message} {before_snippet[:500]}"
        embedding = _get_embedding_model().encode(embed_text)

        try:
            cur = self.conn.execute(
                """INSERT OR REPLACE INTO fix_examples
                   (rule_key, language, severity, issue_message,
                    before_snippet, after_snippet, explanation, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rule_key, language, severity, issue_message,
                 before_snippet[:2000], after_snippet[:2000], explanation,
                 _serialize_embedding(embedding)),
            )
            self.conn.commit()
            return cur.lastrowid or 0
        except Exception:
            return 0

    def retrieve_similar_fixes(
        self,
        rule_key: str,
        issue_message: str,
        language: str = "",
        top_k: int = 3,
        min_score: float = 0.3,
    ) -> List[FixExample]:
        """Retrieve similar past fix examples.

        First tries exact rule_key match, then falls back to semantic similarity.
        """
        results: List[FixExample] = []

        # Phase 1: Exact rule match (highest quality)
        rows = self.conn.execute(
            "SELECT * FROM fix_examples WHERE rule_key = ? ORDER BY created_at DESC LIMIT ?",
            (rule_key, top_k),
        ).fetchall()

        for row in rows:
            results.append(FixExample(
                id=row["id"],
                rule_key=row["rule_key"],
                language=row["language"],
                severity=row["severity"],
                issue_message=row["issue_message"],
                before_snippet=row["before_snippet"],
                after_snippet=row["after_snippet"],
                explanation=row["explanation"],
                score=1.0,  # Exact match
            ))

        if len(results) >= top_k:
            return results[:top_k]

        # Phase 2: Semantic similarity across all examples
        remaining = top_k - len(results)
        seen_ids = {r.id for r in results}

        query_text = f"{rule_key} {issue_message}"
        query_embedding = _get_embedding_model().encode(query_text)

        # Filter by language if provided
        if language:
            rows = self.conn.execute(
                "SELECT * FROM fix_examples WHERE language = ? AND id NOT IN ({})".format(
                    ",".join("?" * len(seen_ids)) if seen_ids else "0"
                ),
                (language, *seen_ids) if seen_ids else (language,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM fix_examples WHERE id NOT IN ({})".format(
                    ",".join("?" * len(seen_ids)) if seen_ids else "0"
                ),
                tuple(seen_ids) if seen_ids else (),
            ).fetchall()

        scored: List[Tuple[float, sqlite3.Row]] = []
        for row in rows:
            if row["embedding"]:
                row_embedding = _deserialize_embedding(row["embedding"])
                score = _cosine_similarity(query_embedding, row_embedding)
                if score >= min_score:
                    scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        for score, row in scored[:remaining]:
            results.append(FixExample(
                id=row["id"],
                rule_key=row["rule_key"],
                language=row["language"],
                severity=row["severity"],
                issue_message=row["issue_message"],
                before_snippet=row["before_snippet"],
                after_snippet=row["after_snippet"],
                explanation=row["explanation"],
                score=score,
            ))

        return results

    def store_standard(
        self,
        source: str,
        title: str,
        content: str,
        language: str = "",
    ) -> int:
        """Store a coding standard document chunk."""
        embed_text = f"{title} {content[:500]}"
        embedding = _get_embedding_model().encode(embed_text)

        try:
            cur = self.conn.execute(
                """INSERT OR REPLACE INTO standard_docs
                   (source, title, content, language, embedding)
                   VALUES (?, ?, ?, ?, ?)""",
                (source, title, content[:5000], language,
                 _serialize_embedding(embedding)),
            )
            self.conn.commit()
            return cur.lastrowid or 0
        except Exception:
            return 0

    def retrieve_standards(
        self,
        query: str,
        language: str = "",
        top_k: int = 3,
        min_score: float = 0.2,
    ) -> List[StandardDoc]:
        """Retrieve relevant coding standard documents."""
        query_embedding = _get_embedding_model().encode(query)

        if language:
            rows = self.conn.execute(
                "SELECT * FROM standard_docs WHERE language = ? OR language = ''",
                (language,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM standard_docs").fetchall()

        scored: List[Tuple[float, sqlite3.Row]] = []
        for row in rows:
            if row["embedding"]:
                row_embedding = _deserialize_embedding(row["embedding"])
                score = _cosine_similarity(query_embedding, row_embedding)
                if score >= min_score:
                    scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: List[StandardDoc] = []
        for score, row in scored[:top_k]:
            results.append(StandardDoc(
                id=row["id"],
                source=row["source"],
                title=row["title"],
                content=row["content"],
                language=row["language"],
                score=score,
            ))

        return results

    def get_stats(self) -> Dict[str, int]:
        """Get counts of stored items."""
        fix_count = self.conn.execute("SELECT COUNT(*) FROM fix_examples").fetchone()[0]
        std_count = self.conn.execute("SELECT COUNT(*) FROM standard_docs").fetchone()[0]
        return {"fix_examples": fix_count, "standard_docs": std_count}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
