import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def rng(request):
    """Seeded RNG for constrained-random tests. Reproduce with FORG_SEED=<n>."""
    seed = int(os.environ.get("FORG_SEED", random.randrange(1 << 30)))
    request.node.user_properties.append(("seed", seed))
    print(f"FORG_SEED={seed}")
    return random.Random(seed)
