from __future__ import annotations
import hashlib
import math
from typing import Sequence


class DeterministicEmbedding:
    """Small deterministic test embedding; replace with a real tenant-safe provider in production."""

    def __init__(self, dimensions: int = 64):
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def embed(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha512(text.casefold().encode("utf-8")).digest()
        values = []
        for i in range(self.dimensions):
            b = digest[i % len(digest)]
            values.append((b / 255.0) * 2.0 - 1.0)
        norm = math.sqrt(sum(v*v for v in values))
        return tuple(v / norm for v in values) if norm else tuple(0.0 for _ in values)
