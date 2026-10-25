"""Seeded random-number generation (§1, §9).
The generator (NumPy PCG64) is part of the reproducibility contract: changing it alters results for the same seed
and therefore requires a major version.
"""

import secrets

import numpy as np

GENERATOR = "PCG64"


def resolve_seed(seed: int | None) -> int:
    """Use the provided seed or generate one to return in ``assumptions``."""
    return secrets.randbits(32) if seed is None else seed


def make_rng(seed: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


def make_streams(seed: int, *counts: int) -> list[list[np.random.Generator]]:
    """Independent generators: one group per entry of ``counts``, one generator per item within a group.

    Stream ``(g, i)`` depends only on the seed, ``g``, and ``i``: adding an item to a group, or a new group at the end,
    leaves every other item's draws unchanged (common random numbers for what-if comparisons).
    """
    groups = np.random.SeedSequence(seed).spawn(len(counts))
    return [[np.random.Generator(np.random.PCG64(s)) for s in g.spawn(n)] for g, n in zip(groups, counts, strict=True)]
