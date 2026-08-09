# Stratpoint ReAct Agent & Tool Orchestrator

This package (`src/stratpoint_rag/agent/`) implements the ReAct reasoning loop that orchestrates proposal generation tools for Stratpoint business development.

---

## 1. ReAct Design Choice

We selected a **lightweight custom ReAct loop** over heavy frameworks like LangChain AgentExecutor or LangGraph for the following reasons:

1. **Zero Heavy Framework Dependencies**: Reduces external coupling and avoids version drift/instability between API and Streamlit UI layers.
2. **Deterministic Tool Routing & Parsing**: Gives precise control over prompt construction, turn limits, and output parsing (especially on Llama 3.1 8B Instruct, which can misroute native function calls).
3. **Resilient Error Recovery**: Implements 1-retry logic per tool call and surfaces error observations back to the LLM instead of crashing the process.
4. **Pluggable Telemetry**: Exposes an `AgentTracer` ABC hook so teammates can inject custom LLMOps tools (LangSmith, Phoenix, MLflow, stdout) without lock-in.

---

## 2. Tool Contracts (Input & Output Schemas)

The orchestrator integrates proposal tools built against typed Pydantic models in `contracts.py`:

### Tool 1: CV / Brief Parser Tool (`parse_client_brief`)
- **Owner**: `cv_parser` teammate
- **Marker**: `# TODO(teammate - cv_parser): replace stub marker`
- **Input**: `BriefParserInput(file_path: str, client_name: str | None = None)`
- **Output**: `ExtractedRequirements(client_name: str, project_name: str, target_platform: list[str], features: list[str], constraints: list[str], tech_stack: list[str], complexity: str)`

### Tool 2: Proposal Scoping Calculator Tool (`estimate_cost_and_timeline`)
- **Owner**: `scoping_calculator`
- **Marker**: `# TODO(teammate - scoping_calculator): replace stub body...`
- **Input**: `EstimationInput(features: list[str], target_platform: list[str], complexity: str = "medium")`
- **Output**: `EstimationResult(total_cost_usd: float, estimated_weeks: float, role_breakdown: list[RoleBreakdownItem], phase_timeline: list[PhaseTimelineItem], summary: str)`

### Tool 3: Proposal PDF Generation Tool (`generate_proposal_pdf`) — **built**
- **Owner**: `pdf_gen` (implemented in `src/stratpoint_rag/pdf_gen/`)
- **Input**: `ProposalPDFInput(client_name: str | None, project_name: str | None, requirements: ExtractedRequirements | dict | None, estimation: EstimationResult | dict | None, output_path: str | None)`
- **Output**: `PDFGenerationResult(pdf_path: str, file_size_bytes: int, download_url: str, status: str)`
- `requirements`/`estimation` are optional: the tool falls back to the turn's
  capture sink, because the model routinely re-calls it having forgotten what
  the estimator returned two turns ago.
- The session id is **bound into the tool** by `build_tool_specs(briefs, names,
  session_id)`, never taken as a tool argument — anything the model can type is
  free text. It scopes the file to `data/proposals/<session_id>/`.
- A failed render **raises**; it does not return `status="failed"`. The loop
  turns an exception into an Observation, whereas a failed result reads as a
  success everywhere downstream. See `CLAUDE.md` → "Proposal PDF".

---

## 3. How Teammates Replace Stubs

To replace a stub with a real implementation:
1. Locate the tool function in `src/stratpoint_rag/agent/tools.py`.
2. Find the `# TODO(owner): replace stub marker`.
3. Replace the stub function body with real tool calls (e.g. calling into `cv_parser`, `scoping_calculator`, or `pdf_gen`).
4. Maintain the typed return type (Pydantic model) so the orchestrator loop and tests continue to work without modification.

---

## 4. How to Run Tests

Run agent unit tests using `pytest` or `uv`:

```bash
# Run all agent unit tests
uv run pytest tests/agent/

# Run specific test suite
uv run pytest tests/agent/test_proposal_agent.py -v
```
