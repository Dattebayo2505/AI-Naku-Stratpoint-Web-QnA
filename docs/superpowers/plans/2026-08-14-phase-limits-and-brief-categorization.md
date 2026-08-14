# Proposal Phase Limits & Brief Categorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Limit roadmap execution phases per proposal based on LLM brief categorization into **Easy** (Max 4 phases), **Standard** (Max 6 phases), and **Hard** (Max 9 phases), preventing 50+ phase bloat while preserving `handbook.md` role rates.

**Architecture:** Update `ExtractedRequirements` schema and Hop 2 extraction prompts in `docparse` to sort briefs by project attributes. Update `_build_dynamic_phases` and `estimate_cost_and_timeline` in `agent/tools.py` to enforce strict phase ceilings and thematic feature grouping.

**Tech Stack:** Python 3.13, Pydantic v2, Pytest, Playwright Chromium (PDF gen).

---

### Task 1: Update Schema & Types for Brief Categorization

**Files:**
- Modify: `src/stratpoint_rag/docparse/schema.py:10-40`
- Modify: `src/stratpoint_rag/agent/contracts.py:62-90`
- Create/Modify: `tests/test_dynamic_phases.py`

- [ ] **Step 1: Write the failing test for schema updates**

```python
from stratpoint_rag.docparse.schema import ExtractedRequirements

def test_extracted_requirements_category_schema():
    reqs = ExtractedRequirements(
        features=["User Login", "Dashboard"],
        platforms=["Web"],
        complexity="easy",
        project_category="easy",
        category_attributes={"max_phases": 4, "feature_tier": "small"}
    )
    assert reqs.project_category == "easy"
    assert reqs.category_attributes["max_phases"] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_phases.py -k test_extracted_requirements_category_schema -v`
Expected: FAIL (missing fields `project_category` / `category_attributes`)

- [ ] **Step 3: Update `ExtractedRequirements` in `docparse/schema.py`**

```python
class ExtractedRequirements(BaseModel):
    features: list[str] = Field(..., description="Feature requirements extracted from the brief.")
    platforms: list[str] = Field(default_factory=lambda: ["Web"], description="Target platforms.")
    complexity: Literal["easy", "standard", "hard", "low", "medium", "high"] = Field(
        "standard", description="Project difficulty level."
    )
    project_category: Literal["easy", "standard", "hard"] = Field(
        "standard", description="Sorted brief category determining phase limits."
    )
    category_attributes: dict[str, Any] = Field(
        default_factory=dict, description="Sorting criteria and attribute breakdown."
    )
    constraints: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    currency_code: str | None = Field(None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_phases.py -k test_extracted_requirements_category_schema -v`
Expected: PASS

- [ ] **Step 5: Commit locally (do NOT push)**

```bash
git add src/stratpoint_rag/docparse/schema.py tests/test_dynamic_phases.py
git commit -m "feat(docparse): add project_category and category_attributes to ExtractedRequirements"
```

---

### Task 2: Update LLM Hop 2 Brief Extraction Prompt

**Files:**
- Modify: `src/stratpoint_rag/docparse/extract.py:50-120`
- Test: `tests/test_docparse_extract.py` or `tests/test_dynamic_phases.py`

- [ ] **Step 1: Write test for Hop 2 categorization prompt mapping**

```python
from stratpoint_rag.docparse.extract import _clean_complexity

def test_complexity_and_category_mapping():
    assert _clean_complexity("easy") == "easy"
    assert _clean_complexity("low") == "easy"
    assert _clean_complexity("medium") == "standard"
    assert _clean_complexity("high") == "hard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_phases.py -k test_complexity_and_category_mapping -v`
Expected: FAIL (`_clean_complexity` not found or outdated)

- [ ] **Step 3: Update Hop 2 system prompt and extraction in `docparse/extract.py`**

In `src/stratpoint_rag/docparse/extract.py`, update the prompt instructions so the LLM categorizes the brief into:
- `easy`: ≤5 features, single platform, standard CRUD/UI, ≤6 weeks.
- `standard`: 6-15 features, multi-platform, standard APIs, 6-12 weeks.
- `hard`: >15 features or complex AI/ML/Cloud/DevOps/legacy integrations, >12 weeks.

And update helper `_clean_complexity`:
```python
def _clean_complexity(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in ("easy", "low", "simple"):
        return "easy"
    if s in ("hard", "high", "complex"):
        return "hard"
    return "standard"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_phases.py -k test_complexity_and_category_mapping -v`
Expected: PASS

- [ ] **Step 5: Commit locally (do NOT push)**

```bash
git add src/stratpoint_rag/docparse/extract.py tests/test_dynamic_phases.py
git commit -m "feat(docparse): update Hop 2 extraction prompt and complexity categorization"
```

---

### Task 3: Implement Phase Limits & Feature Consolidation in Scoping Calculator

**Files:**
- Modify: `src/stratpoint_rag/agent/tools.py:595-665` (`_build_dynamic_phases`)
- Modify: `src/stratpoint_rag/agent/tools.py:770-795` (`estimate_cost_and_timeline`)
- Test: `tests/test_dynamic_phases.py`

- [ ] **Step 1: Write failing tests for phase limits**

```python
from stratpoint_rag.agent.tools import _build_dynamic_phases

def test_phase_limit_hard_project():
    features = [f"Feature {i}" for i in range(1, 51)] # 50 features
    phases = _build_dynamic_phases(features, weeks=16.0, complexity="hard")
    assert len(phases) <= 9, f"Expected <= 9 phases for Hard, got {len(phases)}"

def test_phase_limit_standard_project():
    features = [f"Feature {i}" for i in range(1, 15)] # 14 features
    phases = _build_dynamic_phases(features, weeks=8.0, complexity="standard")
    assert len(phases) <= 6, f"Expected <= 6 phases for Standard, got {len(phases)}"

def test_phase_limit_easy_project():
    features = [f"Feature {i}" for i in range(1, 5)] # 4 features
    phases = _build_dynamic_phases(features, weeks=4.0, complexity="easy")
    assert len(phases) <= 4, f"Expected <= 4 phases for Easy, got {len(phases)}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_phases.py -k "test_phase_limit" -v`
Expected: FAIL (50 features currently creates 27+ phases)

- [ ] **Step 3: Update `_build_dynamic_phases` with hard ceilings and thematic feature chunking**

```python
MAX_PHASE_CAPS = {
    "easy": 4,
    "low": 4,
    "standard": 6,
    "medium": 6,
    "hard": 9,
    "high": 9,
}

def _build_dynamic_phases(features: list[str], weeks: float, complexity: str = "standard") -> list[PhaseTimelineItem]:
    cleaned_feats = _clean_feature_list(features)
    n = len(cleaned_feats)

    comp_key = (complexity or "standard").strip().lower()
    max_allowed = MAX_PHASE_CAPS.get(comp_key, 6)

    # Easy / Rapid projects with <= 3 features
    if n <= 2 and weeks <= 4.0:
        feat_summary = ", ".join(cleaned_feats[:2])
        return [
            PhaseTimelineItem(
                phase_name="Phase 1: Discovery & Rapid Setup",
                duration_weeks=round(weeks * 0.50, 1),
                milestones=["Architecture & Specs", f"Deliverables: {feat_summary}"],
            ),
            PhaseTimelineItem(
                phase_name="Phase 2: QA, Testing & Deployment",
                duration_weeks=round(weeks * 0.50, 1),
                milestones=["End-to-End Testing", "Production Launch & Handoff"],
            ),
        ]

    # Dev budget is total cap minus 2 fixed anchor phases (Discovery & Launch)
    dev_budget = max(1, max_allowed - 2)

    phases: list[PhaseTimelineItem] = []

    # Phase 1: Discovery
    p1_duration = round(max(0.5, weeks * 0.15), 1)
    phases.append(
        PhaseTimelineItem(
            phase_name="Phase 1: Discovery, Strategy & System Architecture",
            duration_weeks=p1_duration,
            milestones=["Technical Architecture Specification", "UI/UX Wireframes & Project Roadmap"],
        )
    )

    # Calculate features per dev phase to respect dev_budget
    import math
    chunk_size = max(1, math.ceil(n / dev_budget))
    feat_chunks = [cleaned_feats[i:i + chunk_size] for i in range(0, n, chunk_size)]

    dev_budget_weeks = max(1.0, weeks * 0.70)
    per_phase_weeks = round(max(0.5, dev_budget_weeks / max(1, len(feat_chunks))), 1)

    for idx, chunk in enumerate(feat_chunks, start=2):
        chunk_title = " & ".join(chunk[:2])
        if len(chunk) > 2:
            chunk_title += f" (+{len(chunk)-2} more)"
        if len(chunk_title) > 45:
            chunk_title = chunk_title[:42].rstrip() + "..."
        milestone_items = [f"Deliverable: {item}" for item in chunk[:4]]
        phases.append(
            PhaseTimelineItem(
                phase_name=f"Phase {idx}: Development — {chunk_title}",
                duration_weeks=per_phase_weeks,
                milestones=milestone_items,
            )
        )

    # Final Phase: QA & Deployment
    p_final_num = len(phases) + 1
    p_final_duration = round(max(0.5, weeks * 0.15), 1)
    phases.append(
        PhaseTimelineItem(
            phase_name=f"Phase {p_final_num}: QA, Security Audit & Production Launch",
            duration_weeks=p_final_duration,
            milestones=["End-to-End QA Regression Testing", "Security Audit & Performance Optimization", "Production Deployment"],
        )
    )

    # Post-processor hard clamp fallback
    while len(phases) > max_allowed:
        # Merge two dev phases in the middle
        p_merge_a = phases[1]
        p_merge_b = phases[2]
        merged_phase = PhaseTimelineItem(
            phase_name=p_merge_a.phase_name,
            duration_weeks=round(p_merge_a.duration_weeks + p_merge_b.duration_weeks, 1),
            milestones=p_merge_a.milestones + p_merge_b.milestones,
        )
        phases = [phases[0]] + [merged_phase] + phases[3:]

    return phases
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_phases.py -k "test_phase_limit" -v`
Expected: PASS

- [ ] **Step 5: Commit locally (do NOT push)**

```bash
git add src/stratpoint_rag/agent/tools.py tests/test_dynamic_phases.py
git commit -m "feat(scoping): enforce phase limits (Easy: 4, Standard: 6, Hard: 9) and thematic feature chunking"
```

---

### Task 4: Verify Full PDF Proposal Render & Handbook Rates

**Files:**
- Test: `tests/test_pdf_service.py`
- Test: `tests/test_dynamic_phases.py`

- [ ] **Step 1: Write integration test for proposal PDF rendering with capped phases**

```python
from stratpoint_rag.agent.contracts import EstimationResult, PhaseTimelineItem, RoleBreakdownItem
from stratpoint_rag.pdf_gen import build_quote_context, render_quote_html, generate_pdf_from_html
import tempfile
from pathlib import Path

def test_proposal_pdf_render_with_capped_phases():
    phases = [
        PhaseTimelineItem(phase_name=f"Phase {i}: Test Phase Name", duration_weeks=2.0, milestones=["Deliverable A", "Deliverable B"])
        for i in range(1, 10) # 9 phases max (Hard)
    ]
    est = EstimationResult(
        total_cost_usd=15000.0,
        currency_code="USD",
        estimated_weeks=18.0,
        role_breakdown=[RoleBreakdownItem(role="Tech Lead", estimated_hours=100, hourly_rate=70, total_cost=7000)],
        phase_timeline=phases,
        summary="Capped phase test proposal"
    )
    context = build_quote_context(
        proposal_id="test001",
        estimation=est,
        client_name="Acme Corp",
        project_name="Enterprise Platform"
    )
    html = render_quote_html(context)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        out_path = Path(tmp.name)
    generate_pdf_from_html(html, out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 5000
    out_path.unlink()
```

- [ ] **Step 2: Run test to verify PDF renders cleanly**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_pdf_service.py tests/test_dynamic_phases.py -v`
Expected: PASS

- [ ] **Step 3: Commit locally (do NOT push)**

```bash
git add tests/test_dynamic_phases.py
git commit -m "test(pdf_gen): verify proposal PDF renders cleanly with 9 capped roadmap phases"
```

---

### Task 5: Execute Full Test Suite & Verification

- [ ] **Step 1: Run complete test suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Final local commit check (Confirm NO git push)**

```bash
git status
```
Confirm working tree clean, local commits ready for user to push when ready.
