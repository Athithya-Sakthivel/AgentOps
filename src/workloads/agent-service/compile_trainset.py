"""Precompile the DSPy triage program
Uses BootstrapFewShot on the training set from data/triage_trainset.json.
The compiled program is saved to compiled/triage_program.json and loaded
at runtime without any LLM calls.

"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import dspy
from dspy.teleprompt import BootstrapFewShot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("compile")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    log.error("GROQ_API_KEY environment variable is not set")
    sys.exit(1)

COMPILED_DIR = Path("compiled")
COMPILED_DIR.mkdir(exist_ok=True)
TRAINSET_PATH = Path("data/triage_trainset.json")


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


def triage_metric(example, pred, trace=None):
    """Weighted accuracy metric."""
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


# Rate limit wrapper for Groq free tier (8,000 TPM)
_last_call_time: float = 0.0
MIN_INTERVAL_SECONDS = 30.0


def rate_limited_metric(example, pred, trace=None):
    """Wrap triage_metric with a 30-second delay between calls."""
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_INTERVAL_SECONDS:
        wait = MIN_INTERVAL_SECONDS - elapsed
        log.info("Rate limiting: waiting %.1fs before next example...", wait)
        time.sleep(wait)
    _last_call_time = time.time()
    return triage_metric(example, pred, trace)


def main():
    log.info("Loading trainset from %s", TRAINSET_PATH)
    with open(TRAINSET_PATH) as f:
        data = json.load(f)

    trainset = []
    for item in data:
        trainset.append(
            dspy.Example(
                query=item["query"],
                safety=item["safety"],
                intent=item["intent"],
                urgency=item["urgency"],
                sentiment=item["sentiment"],
                auto_resolvable=item["auto_resolvable"],
            ).with_inputs("query")
        )

    log.info("Loaded %d training examples", len(trainset))

    # GPT-OSS 20B on Groq — free tier with rate limiting
    lm = dspy.LM(
        "groq/openai/gpt-oss-20b",
        api_key=GROQ_API_KEY,
        temperature=0.0,
        max_tokens=2048,
        num_retries=3,
    )
    dspy.configure(lm=lm)

    program = TriageProgram()

    log.info(
        "Compiling with BootstrapFewShot (rate-limited, ~%d min for %d examples)...",
        (len(trainset) * MIN_INTERVAL_SECONDS) // 60,
        len(trainset),
    )
    optimizer = BootstrapFewShot(
        metric=rate_limited_metric,
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
