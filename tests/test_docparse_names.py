"""The client/project name scan: a suggestion, never an adopted fact."""

import pytest

from stratpoint_rag.docparse.names import suggest_names


def test_reads_a_labelled_client_and_project():
    md = "## Page 1\nClient: Northwind Retail\nProject Name: Loyalty App\n"
    seen = suggest_names(md)

    assert seen.client_name == "Northwind Retail"
    assert seen.project_name == "Loyalty App"


@pytest.mark.parametrize(
    "line",
    [
        "Prepared for: Northwind Retail",
        "**Client:** Northwind Retail",
        "> Customer: Northwind Retail",
        "### Client Name: Northwind Retail",
        "Submitted to: Northwind Retail",
    ],
)
def test_accepts_the_usual_cover_page_phrasings(line):
    assert suggest_names(line).client_name == "Northwind Retail"


def test_markdown_links_are_stripped_to_their_label():
    """A planted `[label](url.pdf)` must not smuggle a URL onto a heading — or
    into anything downstream that scrapes links out of text."""
    seen = suggest_names("Client: [Northwind](https://evil.example/x.pdf)")

    assert seen.client_name == "Northwind"


@pytest.mark.parametrize("value", ["TBD", "N/A", "none", "unknown", "Client"])
def test_placeholders_are_not_names(value):
    assert suggest_names(f"Client: {value}").client_name is None


def test_a_value_with_no_letters_is_not_a_name():
    assert suggest_names("Client: 2026-07-01").client_name is None


def test_an_overlong_value_is_rejected():
    assert suggest_names("Client: " + "x" * 200).client_name is None


def test_a_document_that_states_nothing_suggests_nothing():
    seen = suggest_names("## Page 1\nWe need a mobile app with SSO.\n")

    assert seen.is_empty
    assert seen.client_name is None and seen.project_name is None


def test_the_first_plausible_value_wins():
    """Briefs put the identification on the cover page. 'Best' over a document
    this varied would be guessing dressed up as ranking."""
    md = "Client: Northwind Retail\n\nClient: Someone Else\n"

    assert suggest_names(md).client_name == "Northwind Retail"


def test_a_table_row_keeps_only_the_first_field():
    assert suggest_names("| Client: Northwind | Date: 2026 |").client_name == "Northwind"
