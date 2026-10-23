"""
Dual-Mode Hybrid Embedding Generator for Memora Vector Search.
Primary: Gemini Embedding API (gemini-embedding-2 / text-embedding-004, 384-dimensional).
Fallback: Zero-latency deterministic 384-dimensional signed feature hashing.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Function words carry no topical signal but appear in almost every text, so
# leaving them in inflates the similarity of documents that have nothing in
# common. A real model learns this; a hashing embedding has to be told.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "did", "do",
    "does", "for", "from", "how", "in", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "was", "were", "what", "when", "which",
    "who", "why", "with",
})

_TOKEN_KEEP = set("abcdefghijklmnopqrstuvwxyz0123456789-_")

try:
    from google import genai
    from google.genai import types
    _GENAI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    types = None  # type: ignore
    _GENAI_AVAILABLE = False


class EmbeddingGenerator:
    DEFAULT_DIMENSION = 384
    _mode: str = "auto"  # "auto", "gemini", "hashing"
    _last_mode: str = "deterministic_hashing"
    _gemini_client: Optional[Any] = None
    _client_lock = threading.Lock()

    @classmethod
    def set_mode(cls, mode: str) -> None:
        """Set embedding mode: 'auto', 'gemini', or 'hashing'."""
        if mode not in {"auto", "gemini", "hashing"}:
            raise ValueError(f"Invalid embedding mode: {mode}. Must be 'auto', 'gemini', or 'hashing'.")
        cls._mode = mode

    @classmethod
    def get_mode(cls) -> str:
        """Return the mode used in the most recent embedding generation."""
        return cls._last_mode

    @classmethod
    def _get_gemini_api_key(cls) -> Optional[str]:
        """Fetch Gemini API key from settings or environment."""
        try:
            from core.config import settings
            key = getattr(settings, "GEMINI_API_KEY", None)
            if key and key.strip():
                return key.strip()
        except Exception:
            pass
        key = os.getenv("GEMINI_API_KEY")
        if key and key.strip():
            return key.strip()
        return None

    @classmethod
    def _get_gemini_client(cls) -> Optional[Any]:
        """Lazy-initialize and cache the Google GenAI client."""
        if not _GENAI_AVAILABLE:
            return None
        if cls._gemini_client is not None:
            return cls._gemini_client
        with cls._client_lock:
            if cls._gemini_client is not None:
                return cls._gemini_client
            api_key = cls._get_gemini_api_key()
            if not api_key:
                return None
            try:
                cls._gemini_client = genai.Client(api_key=api_key)
                return cls._gemini_client
            except Exception as exc:
                logger.warning("Failed to initialize Google GenAI client: %s", exc)
                return None

    @classmethod
    def _tokenize(cls, text: str) -> List[str]:
        """Lowercase, strip punctuation, and drop pure function words."""
        raw_tokens = []
        for chunk in text.strip().lower().split():
            word = "".join(ch for ch in chunk if ch in _TOKEN_KEEP)
            if word:
                raw_tokens.append(word)
        content_tokens = [t for t in raw_tokens if t not in _STOPWORDS]
        return content_tokens or raw_tokens

    @classmethod
    def generate_deterministic_embedding(
        cls, text: str, dimension: int = DEFAULT_DIMENSION
    ) -> List[float]:
        """
        Generates a normalized, deterministic dense embedding using signed feature hashing.
        Zero-latency offline fallback for when remote model inference is unavailable.
        """
        if not text or not text.strip():
            return [0.0] * dimension

        counts: Dict[str, int] = {}
        for token in cls._tokenize(text):
            counts[token] = counts.get(token, 0) + 1

        vec = [0.0] * dimension
        for token, count in counts.items():
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            bucket = int(digest[:8], 16) % dimension
            sign = 1.0 if (int(digest[8:10], 16) & 1) == 0 else -1.0
            vec[bucket] += sign * math.log1p(count)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [round(v / norm, 6) for v in vec]
        return vec

    @classmethod
    def generate_hashing_embedding(
        cls, text: str, dimension: int = DEFAULT_DIMENSION
    ) -> List[float]:
        """Alias for generate_deterministic_embedding."""
        return cls.generate_deterministic_embedding(text, dimension)

    @classmethod
    def generate_gemini_embedding(
        cls, text: str, dimension: int = DEFAULT_DIMENSION
    ) -> List[float]:
        """
        Calls Gemini Embedding API (gemini-embedding-2 / text-embedding-004) with exact output dimension.
        Raises RuntimeError on failure.
        """
        if not text or not text.strip():
            return [0.0] * dimension

        client = cls._get_gemini_client()
        if client is None:
            raise RuntimeError("Gemini client is not available or GEMINI_API_KEY is not configured.")

        model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
        try:
            from core.config import settings
            model = getattr(settings, "GEMINI_EMBEDDING_MODEL", model)
        except Exception:
            pass

        config = types.EmbedContentConfig(output_dimensionality=dimension)
        response = client.models.embed_content(
            model=model,
            contents=text,
            config=config,
        )
        if not response.embeddings or not response.embeddings[0].values:
            raise RuntimeError("Empty embedding returned by Gemini API.")

        values = response.embeddings[0].values
        norm = math.sqrt(sum(float(v) * float(v) for v in values))
        if norm > 0:
            return [round(float(v) / norm, 6) for v in values]
        return [round(float(v), 6) for v in values]

    @classmethod
    def generate_embedding(
        cls,
        text: str,
        dimension: int = DEFAULT_DIMENSION,
        prefer_gemini: bool = True,
    ) -> List[float]:
        """
        Primary entry point for generating embeddings.
        Dual-mode: Attempts Gemini Embedding API first; automatically fails soft
        to zero-latency deterministic signed feature hashing on any error or offline state.
        """
        if not text or not text.strip():
            return [0.0] * dimension

        # Forced hashing mode (explicit class mode or environment override)
        if cls._mode == "hashing" or not prefer_gemini:
            cls._last_mode = "deterministic_hashing"
            return cls.generate_deterministic_embedding(text, dimension)

        env_mode = os.getenv("MEMORA_EMBEDDING_MODE", "").strip().lower()
        if env_mode == "hashing" and cls._mode == "auto":
            cls._last_mode = "deterministic_hashing"
            return cls.generate_deterministic_embedding(text, dimension)

        effective_mode = cls._mode if cls._mode in {"gemini", "hashing"} else (env_mode or "auto")

        # Attempt Gemini if in auto or gemini mode
        if effective_mode in {"auto", "gemini"}:
            api_key = cls._get_gemini_api_key()
            if api_key and _GENAI_AVAILABLE:
                try:
                    vec = cls.generate_gemini_embedding(text, dimension)
                    cls._last_mode = "gemini"
                    return vec
                except Exception as exc:
                    logger.warning(
                        "Gemini embedding API call failed (%s); failing soft to deterministic feature hashing.",
                        exc,
                    )
                    if effective_mode == "gemini":
                        # If explicitly forced to gemini mode, re-raise
                        raise
            elif effective_mode == "gemini":
                raise RuntimeError("Embedding mode set to 'gemini' but GEMINI_API_KEY is missing or genai SDK unavailable.")

        # Fail-soft fallback: deterministic feature hashing
        cls._last_mode = "deterministic_hashing"
        return cls.generate_deterministic_embedding(text, dimension)


embedding_generator = EmbeddingGenerator()
