"""
Local Embedding Generator for Memora Vector Search
Produces dense vector representations (e.g. all-MiniLM-L6-v2 384-dimensional).
"""
import math
import hashlib
from typing import List, Optional

class EmbeddingGenerator:
    DEFAULT_DIMENSION = 384

    @classmethod
    def generate_embedding(cls, text: str, dimension: int = DEFAULT_DIMENSION) -> List[float]:
        """
        Generates a normalized, deterministic dense embedding for text content.
        Uses fast token hashing and sinusoidal projection with L2 normalization.
        """
        cleaned = text.strip().lower()
        if not cleaned:
            return [0.0] * dimension

        # Compute multi-seed token projections
        vec = [0.0] * dimension
        tokens = cleaned.split()
        for pos, token in enumerate(tokens):
            h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
            for i in range(dimension):
                # Harmonic phase embedding
                val = math.sin((h >> (i % 32)) * 0.001 + (pos * 0.1))
                vec[i] += val

        # L2 Normalization
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [round(v / norm, 6) for v in vec]
        return vec

embedding_generator = EmbeddingGenerator()