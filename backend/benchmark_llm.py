"""Compare local LLMs with fixed synthetic inputs; not a clinical evaluation."""
import argparse
import time

import httpx

from app.agents.intake_agent import assessment_prompt, followup_prompt
from app.agents.state import TRIAGE_LEVELS
from app.core.llm_client import generate, parse_json_response
from config import settings

CASES = (
    ("followup", followup_prompt("Ich habe seit gestern leichten Husten."), 60),
    ("assessment", assessment_prompt("Ich habe seit gestern leichten Husten."), 120),
)


def valid(kind: str, text: str) -> bool:
    try:
        data = parse_json_response(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if kind == "assessment":
        return (
            data.get("triage_score") in TRIAGE_LEVELS
            and isinstance(data.get("summary"), str)
            and bool(data["summary"].strip())
        )
    return isinstance(data.get("complete"), bool) and (
        data["complete"]
        or (isinstance(data.get("question"), str) and bool(data["question"].strip()))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "foundry_local"), required=True)
    parser.add_argument("--base-url", required=True, help="Local service origin, without /v1")
    parser.add_argument("--model", required=True, help="Loaded model ID or Ollama model name")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    settings.llm_provider = args.provider
    settings.llm_base_url = args.base_url
    settings.llm_model = args.model
    print("Synthetic data only. First request may be cold; later requests may be warm.")
    failed = False
    for iteration in range(1, args.repeats + 1):
        for kind, prompt, timeout in CASES:
            start = time.monotonic()
            try:
                text = generate(prompt, timeout=timeout, json_mode=True)
                ok = valid(kind, text)
                failed |= not ok
                print(f"run={iteration} case={kind} seconds={time.monotonic()-start:.2f} schema_valid={ok}")
                print(text)
            except (httpx.HTTPError, ValueError) as exc:
                failed = True
                print(f"run={iteration} case={kind} seconds={time.monotonic()-start:.2f} error={type(exc).__name__}: {exc}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
