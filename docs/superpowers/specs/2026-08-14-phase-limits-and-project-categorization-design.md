# Design Specification: Project Categorization & Phase Limit Constraints for Proposals

## 1. Overview & Problem Statement
When generating proposals for large client briefs with many extracted features, the proposal generator previously generated an un-capped number of roadmap phases (e.g. 57 phases for a 100-feature brief). This resulted in unrealistic proposals that overwhelmed clients and exceeded single-page PDF rendering limits.

This specification introduces:
1. **Brief Categorization**: Automatic LLM-based sorting of client briefs into three difficulty levels (**Easy**, **Standard**, **Hard**) based on structured project attributes.
2. **Phase Limit Constraints**: Strict phase ceilings per level (**Easy: Max 4 phases**, **Standard: Max 6 phases**, **Hard: Max 9 phases**).
3. **Handbook Alignment**: Direct mapping of role rates, QA/DevOps/AI category add-ons, and pricing logic from `handbook.md`.

---

## 2. Taxonomy & Categorization Rules

The LLM (during requirement extraction and proposal estimation) evaluates the extracted features, tech stack, and scope against these attribute criteria:

| Category | Max Phases | Feature Count | Target Platform Scope | Integration & Tech Complexity | Estimated Duration | `handbook.md` Role Profile |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Easy** | **Max 4** | ≤ 5 features | Single platform (Web *or* Mobile) | Standard CRUD, basic UI, no custom AI/ML or complex cloud/legacy backend integrations | ≤ 6.0 weeks | Fullstack Dev + UI/UX Designer + QA |
| **Standard** | **Max 6** | 6 – 15 features | Multi-platform (Web *and* Mobile) | Standard 3rd-party APIs, database integrations, custom roles & auth | 6.1 – 12.0 weeks | Tech Lead + Senior Fullstack + QA + UI/UX |
| **Hard** | **Max 9** | > 15 features | Cross-platform / Enterprise | Complex integrations (AI/ML pipelines, DevOps/Cloud SRE, Data Eng, security audit, legacy ERP) | > 12.0 weeks | Solutions Architect + Tech Lead + Senior Devs + QA Manager + DevOps/AI Specialists |

---

## 3. Architecture & Data Contract Changes

### 3.1 Pydantic Schema Updates (`src/stratpoint_rag/docparse/schema.py` & `src/stratpoint_rag/agent/contracts.py`)
- Update `ExtractedRequirements`:
  - `complexity`: `Literal["easy", "standard", "hard", "low", "medium", "high"]` (maintaining backward compatibility for legacy callers).
  - Add `project_category`: `Literal["easy", "standard", "hard"]`.
  - Add `category_attributes`: `dict[str, Any]` summarizing feature tier, platform count, and complexity hints.

- Update `EstimationInput` ([`src/stratpoint_rag/agent/contracts.py`](file:///C:/Users/Keisha/Desktop/AI-Naku-Stratpoint-Web-QnA/src/stratpoint_rag/agent/contracts.py)):
  - Accept `complexity: str` ("easy", "standard", "hard").
  - Support `max_phases: int | None`.

---

## 4. Phase Consolidation Algorithm (`src/stratpoint_rag/agent/tools.py`)

### 4.1 Phase Cap Mapping
```python
MAX_PHASE_CAPS = {
    "easy": 4,
    "low": 4,
    "standard": 6,
    "medium": 6,
    "hard": 9,
    "high": 9,
}
```

### 4.2 Dynamic Roadmap Generation Flow
1. **Determine Ceiling**: Retrieve `max_allowed_phases` from the categorized project level (4, 6, or 9).
2. **Fixed Anchor Phases**:
   - **Phase 1**: Discovery, Strategy & System Architecture (15% of timeline).
   - **Final Phase** (`Phase N`): QA, Security Audit & Production Launch (15% of timeline).
3. **Mid-Development Budget**: `dev_phase_budget = max_allowed_phases - 2` (2 phases for Easy, 4 for Standard, 7 for Hard).
4. **Feature Consolidation & Chunking**:
   - Group the cleaned feature list into `dev_phase_budget` thematic milestone chunks (e.g. *User Management & Authentication*, *Core Business Engine*, *Integrations & Reporting*).
   - Calculate per-phase duration based on remaining 70% of the project timeline.
5. **Hard Clamping Post-Processor**:
   - If custom LLM phases or generated phases exceed `max_allowed_phases`, iteratively merge adjacent development phases until `len(phases) <= max_allowed_phases`.

---

## 5. Alignment with `handbook.md`

1. **Role Pricing**:
   - Rates sourced from `handbook.md` in PHP (e.g., Tech Lead ₱3,190–₱3,944/hr, Solution Architect ₱3,480–₱4,350/hr, Senior Fullstack ₱2,610–₱3,364/hr, QA Manager ₱1,044–₱1,276/hr, DevOps ₱1,856–₱2,436/hr, AI/ML ₱2,900–₱4,350/hr) or converted to USD at 60 PHP = 1 USD.
2. **Category Costings**:
   - Category-specific handbook add-ons (Cloud, AI/ML, Data, Security, Licenses) are attached to `role_breakdown` based on brief features without inflating phase count beyond the category ceiling.

---

## 6. Testing & Verification Plan

1. **Unit Tests** (`tests/test_dynamic_phases.py`):
   - Test a brief with 50+ features → verify Hard project produces ≤ 9 phases.
   - Test Standard project → verify ≤ 6 phases.
   - Test Easy project → verify ≤ 4 phases.
   - Verify `handbook.md` role rates and category additions integrate seamlessly.
2. **E2E PDF Test** (`tests/test_pdf_service.py`):
   - Render PDF proposal with 4, 6, and 9 phases to verify crisp page layout with no page overflow.
