"""Load the precompiled DSPy triage program from disk.

No LLM calls are made at load time. If the compiled file is missing,
a clear error is raised so the operator knows to run compile_trainset.py first.
"""

from __future__ import annotations

import logging
from pathlib import Path

import dspy

log = logging.getLogger(__name__)

COMPILED_PATH = Path("compiled/triage_program.json")


def load_or_compile_triage(lm=None) -> dspy.Module:
    """Load the precompiled TriageProgram from JSON.

    Args:
        lm: Ignored (kept for backward compatibility). The program is already compiled.

    Returns:
        A DSPy module ready for inference.

    Raises:
        FileNotFoundError: If the compiled file does not exist.
    """
    if not COMPILED_PATH.exists():
        raise FileNotFoundError(
            f"Compiled triage program not found at {COMPILED_PATH}. "
            "Run 'python compile_trainset.py' first."
        )

    log.info("Loading precompiled triage program from %s", COMPILED_PATH)
    program = dspy.Module()
    program.load(str(COMPILED_PATH))
    return program
