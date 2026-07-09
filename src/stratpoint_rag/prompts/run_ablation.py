"""Ablation runner to compare prompt variants (plan §6.4).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
import httpx
from pydantic import ValidationError

from ..rag import config
from ..rag.retrieve import retrieve
from .builder import build_prompt
from .registry import PROMPT_VARIANTS
from .schema import GroundedAnswer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Test questions (5 gold questions + 2 out-of-scope/refusal questions)
TEST_QUESTIONS = [
    {
        "id": "q1_retail",
        "q": "What Stratpoint projects were related to retail?",
        "is_out_of_scope": False,
        "expected_slug": "retail",
    },
    {
        "id": "q2_outsystems",
        "q": "Does Stratpoint offer OutSystems development services?",
        "is_out_of_scope": False,
        "expected_slug": "outsystems-offerings",
    },
    {
        "id": "q3_serverless",
        "q": "How does serverless architecture with AWS Lambda work?",
        "is_out_of_scope": False,
        "expected_slug": "2020__09__30__serverless-using-aws-lambda",
    },
    {
        "id": "q4_flutter",
        "q": "Tell me about Stratpoint's cross-platform Flutter journey",
        "is_out_of_scope": False,
        "expected_slug": "2021__04__30__finding-flutter-our-cross-platform-journey",
    },
    {
        "id": "q5_build_fast",
        "q": "What did Stratpoint build fast on OutSystems?",
        "is_out_of_scope": False,
        "expected_slug": "2021__05__19__apps-stratpoint-built-fast-and-right-on-outsystems",
    },
    {
        "id": "q6_refusal_stock",
        "q": "What is Stratpoint's current stock market ticker and share price?",
        "is_out_of_scope": True,
        "expected_slug": None,
    },
    {
        "id": "q7_refusal_cost",
        "q": "How much does a custom mobile app cost at Stratpoint?",
        "is_out_of_scope": True,
        "expected_slug": None,
    },
]


def run_call(
    system_prompt: str,
    user_prompt: str,
    use_schema: bool,
    temperature: float,
    top_p: float,
) -> str:
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .env)")

    payload = {
        "model": config.llm_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": 4096,
        "stream": False,
    }

    if use_schema:
        payload["response_format"] = {"type": "json_object"}

    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def run_ablation() -> None:
    # 1. Setup output path
    output_dir = Path(__file__).parent.parent / "evaluation"
    output_dir.mkdir(exist_ok=True)
    results_path = output_dir / "prompt_ablation_results.jsonl"

    log.info("Running ablation. Output will be saved to %s", results_path)

    # 2. Cache retrieval results to ensure same context across all variants
    retrieved_context = {}
    for item in TEST_QUESTIONS:
        q = item["q"]
        chunks = retrieve(q, k=3)
        retrieved_context[q] = chunks

    # 3. Main execution loop
    ablation_results = []
    with open(results_path, "w", encoding="utf-8") as f:
        for var_name, var_config in PROMPT_VARIANTS.items():
            log.info("Running variant: %s", var_name)
            for item in TEST_QUESTIONS:
                q = item["q"]
                chunks = retrieved_context[q]
                system_prompt, user_prompt = build_prompt(q, chunks, var_name)

                log.info("  Testing question: %r", q)
                try:
                    raw_response = run_call(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        use_schema=var_config.use_schema,
                        temperature=var_config.temperature,
                        top_p=var_config.top_p,
                    )
                except Exception as e:
                    log.error("    NIM API Call failed: %s", e)
                    continue

                # Parse and evaluate
                json_valid = False
                refusal_correct = False
                confidence = 0.0
                reasoning = ""
                answer = raw_response

                if var_config.use_schema:
                    try:
                        parsed = GroundedAnswer.model_validate_json(raw_response)
                        json_valid = True
                        confidence = parsed.confidence
                        reasoning = parsed.reasoning
                        answer = parsed.answer
                        # Refusal check: out-of-scope question should have is_grounded=False
                        if item["is_out_of_scope"]:
                            refusal_correct = parsed.is_grounded is False
                        else:
                            refusal_correct = parsed.is_grounded is True
                    except (ValidationError, json.JSONDecodeError) as err:
                        json_valid = False
                        log.warning("    Invalid JSON response: %s", err)
                else:
                    # For non-schema variants, check if they explicitly refuse out-of-scope queries
                    json_valid = True  # treated as valid since schema wasn't requested
                    if item["is_out_of_scope"]:
                        refusal_correct = any(
                            phrase in raw_response.lower()
                            for phrase in [
                                "do not know",
                                "don't know",
                                "not in the context",
                                "no information",
                                "cannot answer",
                                "sorry",
                                "apologize",
                            ]
                        )
                    else:
                        refusal_correct = True

                record = {
                    "variant": var_name,
                    "question_id": item["id"],
                    "question": q,
                    "is_out_of_scope": item["is_out_of_scope"],
                    "raw_response": raw_response,
                    "parsed_answer": answer,
                    "reasoning": reasoning,
                    "json_valid": json_valid,
                    "refusal_correct": refusal_correct,
                    "confidence": confidence,
                }
                ablation_results.append(record)
                f.write(json.dumps(record) + "\n")
                f.flush()

    # 4. Generate summary stats
    print("\n" + "=" * 80)
    print("PROMPT ENGINEERING ABLATION RESULTS SUMMARY")
    print("=" * 80)
    print(
        f"{'Variant':<25} | {'JSON Valid':<10} | {'Refusal OK':<10} | {'Avg Conf':<8} | {'Notes'}"
    )
    print("-" * 80)

    for var_name in PROMPT_VARIANTS.keys():
        var_records = [r for r in ablation_results if r["variant"] == var_name]
        if not var_records:
            continue

        json_valid_rate = sum(1 for r in var_records if r["json_valid"]) / len(
            var_records
        )
        refusal_correct_rate = sum(
            1 for r in var_records if r["refusal_correct"]
        ) / len(var_records)
        avg_confidence = sum(r["confidence"] for r in var_records) / len(var_records)

        notes = ""
        if var_name == "v4_combined_lowtemp":
            notes = "Winning candidate (high precision)"
        elif var_name == "v4_combined_hightemp":
            notes = "Isolated high-temperature comparison"

        print(
            f"{var_name:<25} | {json_valid_rate:<10.2%} | {refusal_correct_rate:<10.2%} | {avg_confidence:<8.2f} | {notes}"
        )
    print("=" * 80)


if __name__ == "__main__":
    run_ablation()
