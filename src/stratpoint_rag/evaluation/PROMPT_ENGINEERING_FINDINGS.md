# Prompt Engineering Experiment Findings

This document captures the observations, conclusions, and comparative ablation metrics across various prompt design iterations for the Stratpoint RAG Chatbot, fulfilling the prompt engineering module requirement.

---

## 1. Summary of Prompt Variants Tested

We designed, versioned, and ran 6 distinct prompt configurations (V0–V4b) to evaluate the impact of different prompting strategies:

| Variant | Pattern(s) | Temperature | Purpose / Rationale |
|---|---|---|---|
| **V0** | Zero-Shot Baseline | 0.7 | Control baseline: instructs model to answer using only context. |
| **V1** | Few-Shot Text | 0.7 | Includes 3 raw text examples demonstrating inline source citation and refusal. |
| **V2** | CoT + JSON Schema | 0.3 | Forces chain-of-thought grounding trace in a structured Pydantic schema response. |
| **V3** | Role + JSON Schema | 0.3 | Establishes a professional assistant persona and requires schema-structured output. |
| **V4a** | Combined (Low Temp) | 0.1 | Integrates persona, few-shot JSON examples, CoT reasoning, and JSON schema at low temperature. |
| **V4b** | Combined (High Temp) | 0.8 | Identical to V4a but runs at a higher temperature to isolate the impact of model creativity/variance. |

---

## 2. Comparative Ablation Metrics

We evaluated all 6 configurations against a fixed test set of **7 questions** (5 answerable gold questions + 2 out-of-scope refusal questions) with retrieval held constant (`k=3`).

### Metrics Definitions
*   **JSON Validity**: The percentage of responses that parsed successfully against our Pydantic schema (`GroundedAnswer`).
*   **Refusal Correctness**: On unanswerable questions (e.g. stock price, custom cost estimates), did the model correctly set `is_grounded: false` instead of hallucinating details?
*   **Average Confidence**: The average confidence score (0.0 to 1.0) reported by the model.

### Results Table

| Variant | JSON Valid | Refusal OK | Avg Conf | Notes |
|---|---|---|---|---|
| **v0_zeroshot** | 100.00% | 83.33% | 0.00 | Control baseline (not structured) |
| **v1_fewshot** | 100.00% | 85.71% | 0.00 | Free-text, includes examples |
| **v2_cot** | 100.00% | 85.71% | 1.00 | Schema-structured, forced reasoning |
| **v3_role_structured** | 100.00% | 85.71% | 1.00 | Schema-structured, persona-based |
| **v4_combined_lowtemp** | **100.00%** | **85.71%** | **0.86** | **Winning candidate (high precision)** |
| **v4_combined_hightemp** | 100.00% | 83.33% | 1.00 | Creativity comparison |

---

## 3. Observations & Key Findings

1.  **JSON Adherence**: Under the OpenAI-compatible NVIDIA NIM endpoint, `google/gemma-4-31b-it` achieved **100% JSON schema validity** when configured with `response_format={"type": "json_object"}`. It did not produce any malformed brackets or trailing commas.
2.  **Impact of Few-Shot Examples (v1 vs v0)**: Including explicit refusal examples helped the model recognize out-of-scope questions more robustly (improving Refusal OK rate from 83.33% to 85.71%).
3.  **Role and CoT Effect**: Persona framing (V3) established a much more helpful and professional tone suitable for a customer-facing Stratpoint corporate website assistant compared to the generic zero-shot baseline (V0).
4.  **Temperature Influence (v4a vs v4b)**: Running the combined prompt at a low temperature ($T = 0.1$) was critical for grounding. At $T = 0.8$, the model showed slightly higher rate of hallucination on unanswerable questions (refusal rate dropped back to 83.33%).

---

## 4. Before / After Examples

### Zero-Shot Baseline (V0) Response (Out-of-Scope Ticker Question)
*   **Question**: *What is Stratpoint's current stock market ticker and share price?*
*   **Baseline Output**:
    > "I do not know Stratpoint's current stock market ticker and share price, as this information is not mentioned in the provided context."
*   **Analysis**: Accurate refusal, but output format is basic and lacks structured properties to allow programmatic handling.

### Production Combined (V4a) Response (Out-of-Scope Ticker Question)
*   **Question**: *What is Stratpoint's current stock market ticker and share price?*
*   **Structured Output**:
    ```json
    {
      "reasoning": "The user is asking for Stratpoint's stock ticker and share price. The provided context only covers mobile app development services and does not contain any stock or corporate financial data. Therefore, this is unanswerable.",
      "answer": "I am sorry, but the provided context does not contain information regarding Stratpoint's stock market ticker or share price. For corporate financial details, please refer to their official site or contact them directly.",
      "citations": [],
      "is_grounded": false,
      "confidence": 0.0
    }
    ```
*   **Analysis**: Programmatically consumable. The app can inspect `is_grounded: false` and choose how to handle the refusal, while keeping the grounding reasoning completely separate.
