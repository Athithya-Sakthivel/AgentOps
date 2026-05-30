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


class TriageSignature(dspy.Signature):
    """Classify a customer message for safety, intent, urgency, sentiment, and auto-resolvability."""

    query: str = dspy.InputField()
    safety: str = dspy.OutputField(desc="SAFE or UNSAFE")
    intent: str = dspy.OutputField()
    urgency: int = dspy.OutputField(desc="1-10")
    sentiment: str = dspy.OutputField(desc="angry, frustrated, confused, neutral, satisfied")
    auto_resolvable: bool = dspy.OutputField()


class TriageProgram(dspy.Module):
    def __init__(self):
        super().__init__()
        self.classify = dspy.ChainOfThought(TriageSignature)

    def forward(self, query: str):
        result = self.classify(query=query)
        return dspy.Prediction(
            safety=result.safety,
            intent=result.intent,
            urgency=int(result.urgency),
            sentiment=result.sentiment,
            auto_resolvable=bool(result.auto_resolvable),
        )


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
    program = TriageProgram()
    program.load(str(COMPILED_PATH))
    return program
