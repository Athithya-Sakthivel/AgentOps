"""Precompile the DSPy triage program using Bedrock (no rate limits).

Uses BootstrapFewShot on the training set from data/triage_trainset.json.
The compiled program is saved to compiled/triage_program.json and loaded
at runtime without any LLM calls.

Usage:
    python compile_trainset.py
"""

import json
import logging
from pathlib import Path

import dspy
from dspy.teleprompt import BootstrapFewShot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("compile")

COMPILED_DIR = Path("compiled")
COMPILED_DIR.mkdir(exist_ok=True)
TRAINSET_PATH = Path("data/triage_trainset.json")


class TriageSignature(dspy.Signature):
    """Classify a customer message for safety, intent, urgency, sentiment,
    auto-resolvability, and the required tool/action if any."""

    query: str = dspy.InputField()
    safety: str = dspy.OutputField(desc="SAFE or UNSAFE")
    intent: str = dspy.OutputField()
    urgency: int = dspy.OutputField(desc="1-10")
    sentiment: str = dspy.OutputField(desc="angry, frustrated, confused, neutral, satisfied")
    auto_resolvable: bool = dspy.OutputField()
    required_action: str = dspy.OutputField(
        desc="Short description of the required action, or empty string"
    )
    required_tool: str = dspy.OutputField(desc="MCP tool name to call, or empty string")


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
            required_action=getattr(result, "required_action", "") or "",
            required_tool=getattr(result, "required_tool", "") or "",
        )


def triage_metric(example, pred, trace=None):
    """Weighted accuracy metric for the core fields."""
    score = 0.0
    if pred.safety == example.safety:
        score += 0.3
    if pred.intent == example.intent:
        score += 0.3
    if abs(pred.urgency - example.urgency) <= 1:
        score += 0.2
    if pred.sentiment == example.sentiment:
        score += 0.1
    if pred.auto_resolvable == example.auto_resolvable:
        score += 0.1
    return score


def main():
    log.info("Loading trainset from %s", TRAINSET_PATH)
    with open(TRAINSET_PATH) as f:
        data = json.load(f)

    trainset = []
    for item in data:
        example = dspy.Example(
            query=item["query"],
            safety=item["safety"],
            intent=item["intent"],
            urgency=item["urgency"],
            sentiment=item["sentiment"],
            auto_resolvable=item["auto_resolvable"],
        )
        # Attach new fields if present, otherwise default
        example = example.with_inputs("query")
        example.required_action = item.get("required_action", "")
        example.required_tool = item.get("required_tool", "")
        trainset.append(example)

    log.info("Loaded %d training examples", len(trainset))

    # Use Bedrock Llama 3 8B for compilation (same as runtime)
    lm = dspy.LM(
        "bedrock/meta.llama3-8b-instruct-v1:0",
        temperature=0.0,
        max_tokens=2048,
    )
    dspy.configure(lm=lm)

    program = TriageProgram()

    log.info("Compiling with BootstrapFewShot (no rate limits) …")
    optimizer = BootstrapFewShot(
        metric=triage_metric,
        max_bootstrapped_demos=4,
        max_labeled_demos=16,
        max_errors=10,
    )
    compiled = optimizer.compile(program, trainset=trainset)

    output_path = COMPILED_DIR / "triage_program.json"
    compiled.save(str(output_path))
    log.info("Saved compiled program to %s", output_path)
    log.info("Done.")


if __name__ == "__main__":
    main()
