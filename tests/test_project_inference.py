"""Unit tests for project solution type inference engine."""

import pytest

from stratpoint_rag.agent.contracts import ExtractedRequirements
from stratpoint_rag.pdf_gen.mapping import infer_project_title


def test_infer_project_title_web_app():
    req = ExtractedRequirements(
        features=["User Authentication", "Admin Dashboard", "Reporting Analytics"],
        target_platform=["Web"],
    )
    title = infer_project_title(req)
    assert title == "Software Services — Full-Stack Web Application"


def test_infer_project_title_mobile_app():
    req = ExtractedRequirements(
        features=["Push Notifications", "User Account"],
        target_platform=["Mobile", "iOS", "Android"],
    )
    title = infer_project_title(req)
    assert title == "Software Services — Mobile Application (iOS/Android)"


def test_infer_project_title_ecommerce():
    req = ExtractedRequirements(
        features=["Product Catalog", "Shopping Cart", "Stripe Checkout"],
        target_platform=["Web"],
    )
    title = infer_project_title(req)
    assert title == "Software Services — E-Commerce Web Application"


def test_infer_project_title_ai():
    req = ExtractedRequirements(
        features=["AI Recommendations", "LLM Model Tuning", "RAG Pipeline"],
        target_platform=["Web"],
    )
    title = infer_project_title(req)
    assert title == "Artificial Intelligence — AI/ML Engineering & Model Solutions"


def test_infer_project_title_website_only():
    req = ExtractedRequirements(
        features=["Corporate Website", "WordPress CMS", "Landing Page"],
        target_platform=["Web"],
    )
    title = infer_project_title(req)
    assert title == "Software Services — Custom Website & CMS"


def test_infer_project_title_explicit_override():
    req = ExtractedRequirements(
        features=["User Dashboard"],
        target_platform=["Web"],
    )
    title = infer_project_title(req, project_name="Custom Healthcare Portal")
    assert title == "Custom Healthcare Portal"


# ── category keys are matched as whole words ───────────────────────────────
#
# has_ai used `"ai" in all_text`, and all_text includes the whole brief. Any
# document containing "email", "domain", "maintain", "detail" or "main" landed
# in the AI category — and has_ai is tested first, so almost every proposal was
# titled "Artificial Intelligence" regardless of what it was for.


@pytest.mark.parametrize(
    "feature",
    [
        "Email notifications",   # 'ai' inside "Email"
        "Domain and hosting",    # 'ai' inside "Domain"
        "Plain contact form",    # 'ai' inside "Plain"
        "Maintain the site",     # 'ai' inside "Maintain"
        "HTML templates",        # 'ml' inside "HTML"
        "Available 24/7",        # 'ai' inside "Available"
    ],
)
def test_ordinary_words_do_not_title_a_proposal_as_ai(feature):
    req = ExtractedRequirements(features=[feature], target_platform=["Web"])
    assert infer_project_title(req) == "Software Services — Full-Stack Web Application"


def test_genuine_ai_keywords_still_classify_as_ai():
    """The category itself must keep working on real AI briefs."""
    for feats in (
        ["AI Recommendations"],
        ["ML pipeline"],
        ["Machine learning scoring"],
        ["LLM chat assistant"],
        ["RAG over documents"],
    ):
        req = ExtractedRequirements(features=feats, target_platform=["Web"])
        assert infer_project_title(req) == (
            "Artificial Intelligence — AI/ML Engineering & Model Solutions"
        ), feats


# ── "model" and "data" are ordinary procurement boilerplate ────────────────
#
# Whole-word matching removed the email->ai class of false positive, but
# has_ai still keyed on "model" and has_data on "data", and has_ai is tested
# first — so a WordPress brochure site whose brief says "operating model" was
# still headed as an AI/ML engagement.


def test_the_word_model_does_not_title_a_proposal_as_ai():
    req = ExtractedRequirements(
        features=["An operating model and governance model will be agreed"],
        target_platform=["Web"],
        tech_stack=["WordPress"],
    )
    assert infer_project_title(req) != (
        "Artificial Intelligence — AI/ML Engineering & Model Solutions"
    )


def test_the_word_data_alone_does_not_title_a_proposal_as_data_services():
    req = ExtractedRequirements(
        features=["Master data must be migrated by the vendor"],
    )
    assert infer_project_title(req) != (
        "Data Services — Data Engineering & Analytics Platform"
    )


def test_data_engineering_keywords_still_classify_as_data():
    req = ExtractedRequirements(features=["ETL pipeline", "Analytics warehouse"])
    assert infer_project_title(req) == (
        "Data Services — Data Engineering & Analytics Platform"
    )


def test_the_title_is_inferred_from_the_structured_fields_not_the_whole_brief(tmp_path):
    """The whole-document read made the title depend on prose the estimate never
    saw — one 'model' or 'store' anywhere in a 20-page RFP re-categorised it."""
    brief = tmp_path / "transcription.md"
    brief.write_text(
        "An operating model and governance model will be agreed. "
        "Records are kept in a document store.",
        encoding="utf-8",
    )
    req = ExtractedRequirements(
        features=["Contact form"],
        target_platform=["Web"],
        tech_stack=["WordPress"],
        source_markdown_path=str(brief),
    )
    assert infer_project_title(req) == "Software Services — Custom Website & CMS"


# ── whole-word matching must still see plurals ────────────────────────────
#
# The guard keys sit inside a `not has(...)`, so losing "dashboards" and
# "microservices" made the website-only branch *more* permissive.


def test_plural_guard_keys_keep_a_microservices_brief_out_of_the_cms_branch():
    req = ExtractedRequirements(
        features=["WordPress CMS front-end with admin dashboards", "microservices backend"],
        target_platform=["Web"],
    )
    assert infer_project_title(req) == "Software Services — Full-Stack Web Application"
