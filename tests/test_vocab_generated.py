"""The committed `vocab.generated.ts` is what `dev/gen_vocab.py` writes, and
what it writes does not depend on the process's hash seed."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("gen_vocab", ROOT / "dev" / "gen_vocab.py")
gen = importlib.util.module_from_spec(_SPEC)
sys.modules["gen_vocab"] = gen
_SPEC.loader.exec_module(gen)


def test_the_committed_file_is_what_the_generator_writes():
    assert gen.OUT.read_text() == gen.render(), "run `just vocab` and commit the result"


def test_the_output_does_not_depend_on_the_hash_seed():
    outs = {
        subprocess.run(
            [sys.executable, str(ROOT / "dev" / "gen_vocab.py"), "--print"],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert len(outs) == 1
