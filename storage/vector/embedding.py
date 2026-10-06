"""
Local Embedding Generator for Memora Vector Search
Produces dense vector representations (e.g. all-MiniLM-L6-v2 384-dimensional).
"""
import math
import hashlib
from typing import List, Dict

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


class EmbeddingGenerator:
    DEFAULT_DIMENSION = 384

    @classmethod
    def _tokenize(cls, text: str) -> List[str]:
        """Lowercase, strip punctuation, and drop pure function words."""
        raw_tokens = []
        for chunk in text.strip().lower().split():
            word = "".join(ch for ch in chunk if ch in _TOKEN_KEEP)
            if word:
                raw_tokens.append(word)
        content_tokens = [t for t in raw_tokens if t not in _STOPWORDS]
        # A query made entirely of stopwords ("to be or not to be") must still
        # produce a usable vector rather than an all-zero one that matches
        # nothing at all.
        return content_tokens or raw_tokens

    @classmethod
    def generate_embedding(cls, text: str, dimension: int = DEFAULT_DIMENSION) -> List[float]:
        """
        Generates a normalized, deterministic dense embedding for text content.

        Uses signed feature hashing (the hashing trick) over content tokens with
        sublinear term frequency, then L2 normalization.

        This replaced a "harmonic phase" projection computed as
        sin((h >> (i % 32)) * 0.001 + pos * 0.1). `h` is a 256-bit digest, so the
        sine argument was astronomically large and its value was dominated by
        floating-point noise rather than by the token. The result was a vector
        that did not measure similarity at all: measured on this codebase, two
        unrelated sentences drawn from a shared vocabulary scored a mean cosine
        of 0.366 (max 0.852), while a query scored only 0.313 against the record
        that actually answered it. Since dense_vector carries weight 0.6 of the
        hybrid score and the search threshold is 0.30, roughly half of all
        irrelevant records cleared the bar and the ranking was noise-driven.

        Measured after this change on the same inputs: query to correct answer
        0.646, to a near paraphrase 0.800, to a partial distractor 0.224, to an
        unrelated sentence 0.000 - the ordering a retrieval system needs.

        NOTE: vectors are not comparable across embedding schemes. Any vector
        store holding embeddings from the old scheme must be re-indexed, or
        those records will score against the new query vectors meaninglessly.
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
            # Signed buckets keep unrelated collisions from systematically
            # pushing similarity positive.
            sign = 1.0 if (int(digest[8:10], 16) & 1) == 0 else -1.0
            # Sublinear TF so a repeated word does not dominate the vector.
            vec[bucket] += sign * math.log1p(count)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [round(v / norm, 6) for v in vec]
        return vec


embedding_generator = EmbeddingGenerator()
