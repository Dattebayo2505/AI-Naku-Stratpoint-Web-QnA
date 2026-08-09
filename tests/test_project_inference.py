"""Unit tests for project solution type inference engine."""

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
