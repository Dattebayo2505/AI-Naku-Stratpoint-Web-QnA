"""Unit tests for dynamic phase roadmaps and custom LLM phase support."""

import pytest
from stratpoint_rag.agent import tools
from stratpoint_rag.agent.contracts import EstimationInput, PhaseTimelineItem


def test_estimate_cost_and_timeline_custom_phases():
    """Verify custom LLM-supplied phases are preserved in estimate_cost_and_timeline."""
    custom = [
        PhaseTimelineItem(
            phase_name="Phase 1: Inception & Prototyping",
            duration_weeks=2.0,
            milestones=["PoC Demo", "Architecture Plan"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 2: Alpha Build",
            duration_weeks=4.0,
            milestones=["Backend API", "Core UI"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 3: Beta Testing",
            duration_weeks=3.0,
            milestones=["Security Hardening", "User Feedback"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 4: Launch & Maintenance",
            duration_weeks=1.0,
            milestones=["Production Deploy", "Handoff"],
        ),
    ]

    inp = EstimationInput(
        features=["Custom Feature 1", "Custom Feature 2"],
        target_platform=["Web"],
        complexity="high",
        timeline_weeks=10.0,
        custom_phases=custom,
    )

    res = tools.estimate_cost_and_timeline(inp)
    assert res.estimated_weeks == 10.0
    assert len(res.phase_timeline) == 4
    assert res.phase_timeline[0].phase_name == "Phase 1: Inception & Prototyping"
    assert res.phase_timeline[3].phase_name == "Phase 4: Launch & Maintenance"


def test_estimate_cost_and_timeline_dynamic_phases_short_project():
    """Short projects (<= 3.5 weeks) dynamically generate 2 phases."""
    inp = EstimationInput(
        features=["Landing Page"],
        target_platform=["Web"],
        complexity="low",
        timeline_weeks=3.0,
    )
    res = tools.estimate_cost_and_timeline(inp)
    assert res.estimated_weeks == 3.0
    assert len(res.phase_timeline) == 2
    assert "Discovery" in res.phase_timeline[0].phase_name
    assert "Deployment" in res.phase_timeline[1].phase_name or "Acceptance" in res.phase_timeline[1].phase_name


def test_estimate_cost_and_timeline_dynamic_phases_large_project():
    """Large projects with 4 features generate 6 phases (Discovery + 4 feature phases + Launch)."""
    inp = EstimationInput(
        features=["Feature A", "Feature B", "Feature C", "Feature D"],
        target_platform=["Web", "iOS", "Android"],
        complexity="high",
        timeline_weeks=12.0,
    )
    res = tools.estimate_cost_and_timeline(inp)
    assert res.estimated_weeks == 12.0
    assert len(res.phase_timeline) == 6
    assert "Discovery" in res.phase_timeline[0].phase_name
    assert "Launch" in res.phase_timeline[5].phase_name


def test_estimate_cost_and_timeline_uncapped_phases():
    """5 features dynamically scale to 7 phases with zero maximum cap."""
    inp = EstimationInput(
        features=["Ad Campaign", "Video Ads", "Social Media", "Search Engine Marketing", "Retargeting Analytics"],
        target_platform=["Web"],
        complexity="medium",
        timeline_weeks=10.0,
    )
    res = tools.estimate_cost_and_timeline(inp)
    assert res.estimated_weeks == 10.0
    assert len(res.phase_timeline) == 7
    assert "Discovery" in res.phase_timeline[0].phase_name
    assert "Launch" in res.phase_timeline[6].phase_name


def test_extracted_requirements_category_schema():
    """Verify ExtractedRequirements supports project_category and category_attributes."""
    from stratpoint_rag.docparse.schema import ExtractedRequirements

    req = ExtractedRequirements()
    assert req.project_category == "standard"
    assert req.category_attributes == {}

    req_custom = ExtractedRequirements(
        project_category="hard",
        category_attributes={"estimated_pages": 15, "domain": "fintech"},
        complexity="hard",
    )
    assert req_custom.project_category == "hard"
    assert req_custom.category_attributes == {"estimated_pages": 15, "domain": "fintech"}
    assert req_custom.complexity == "hard"


def test_complexity_and_category_mapping():
    """Verify _clean_complexity maps easy/low/simple to easy, hard/high/complex to hard, and medium/standard/unknown to standard."""
    from stratpoint_rag.docparse.extract import _clean_complexity

    # easy mappings
    assert _clean_complexity("easy") == "easy"
    assert _clean_complexity("low") == "easy"
    assert _clean_complexity("simple") == "easy"
    assert _clean_complexity(" EASY ") == "easy"

    # hard mappings
    assert _clean_complexity("hard") == "hard"
    assert _clean_complexity("high") == "hard"
    assert _clean_complexity("complex") == "hard"
    assert _clean_complexity("HIGH") == "hard"

    # standard mappings (medium, standard, or unknown)
    assert _clean_complexity("medium") == "standard"
    assert _clean_complexity("standard") == "standard"
    assert _clean_complexity("moderate") == "standard"
    assert _clean_complexity("") == "standard"
    assert _clean_complexity(None) == "standard"


