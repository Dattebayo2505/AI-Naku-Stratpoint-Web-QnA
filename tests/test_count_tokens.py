"""Tests for count_tokens.py CLI and formatting logic."""
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from util.count_tokens import (
    FileMetrics,
    TokenSummary,
    compute_metrics,
    format_ascii_table,
    format_ascii_bar_chart,
    parse_args,
    fetch_token_count,
    analyze_file,
)


def test_compute_metrics_calculates_correctly():
    text = "Hello world! This is a test."
    metrics = compute_metrics("sample.md", text, prompt_tokens=8)
    assert metrics.file_path == "sample.md"
    assert metrics.characters == len(text)
    assert metrics.words == 6
    assert metrics.lines == 1
    assert metrics.tokens == 8
    assert metrics.tokens_per_word == pytest.approx(8 / 6, rel=1e-2)
    assert metrics.error is None


def test_token_summary_aggregation():
    m1 = FileMetrics("f1.md", characters=100, words=20, lines=5, tokens=25)
    m2 = FileMetrics("f2.md", characters=300, words=60, lines=15, tokens=75)
    summary = TokenSummary.from_metrics([m1, m2])

    assert summary.total_files == 2
    assert summary.total_chars == 400
    assert summary.total_words == 80
    assert summary.total_lines == 20
    assert summary.total_tokens == 100
    assert summary.avg_tokens_per_word == pytest.approx(100 / 80, rel=1e-2)

    # Share percentages
    assert m1.share_pct == pytest.approx(25.0, rel=1e-2)
    assert m2.share_pct == pytest.approx(75.0, rel=1e-2)


def test_format_ascii_table_contains_files_and_totals():
    m1 = FileMetrics("f1.md", characters=100, words=20, lines=5, tokens=25, share_pct=25.0)
    m2 = FileMetrics("f2.md", characters=300, words=60, lines=15, tokens=75, share_pct=75.0)
    summary = TokenSummary.from_metrics([m1, m2])

    table = format_ascii_table(summary, model_name="meta/llama-3.1-8b-instruct")
    assert "TOKEN COUNT VISUALIZER" in table
    assert "f1.md" in table
    assert "f2.md" in table
    assert "TOTAL" in table
    assert "100" in table  # total tokens


def test_format_ascii_bar_chart():
    m1 = FileMetrics("f1.md", characters=100, words=20, lines=5, tokens=25, share_pct=25.0)
    m2 = FileMetrics("f2.md", characters=300, words=60, lines=15, tokens=75, share_pct=75.0)
    summary = TokenSummary.from_metrics([m1, m2])

    chart = format_ascii_bar_chart(summary, width=40)
    assert "Token Distribution:" in chart
    assert "f1.md" in chart
    assert "f2.md" in chart
    assert "25.0%" in chart
    assert "75.0%" in chart
    assert "#" in chart


def test_parse_args_handles_input_flag():
    args = parse_args(["-i", "file1.md", "file2.md"])
    assert args.files == ["file1.md", "file2.md"]


def test_parse_args_handles_multiple_flags():
    args = parse_args(["-i", "file1.md", "-i", "file2.md", "--model", "custom-model", "--json"])
    assert args.files == ["file1.md", "file2.md"]
    assert args.model == "custom-model"
    assert args.json is True


def test_fetch_token_count_mocked():
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 1,
                "total_tokens": 43,
            }
        }
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tokens = fetch_token_count("Sample text", model="meta/llama-3.1-8b-instruct", api_key="test-key")
        assert tokens == 42
        mock_client.post.assert_called_once()


def test_analyze_file_non_existent(tmp_path):
    metrics = analyze_file(str(tmp_path / "non_existent.md"), model="dummy", api_key="dummy")
    assert metrics.error is not None
    assert "File not found" in metrics.error


def test_main_cli_success(tmp_path, monkeypatch, capsys):
    f1 = tmp_path / "file1.md"
    f1.write_text("This is file one.", encoding="utf-8")
    f2 = tmp_path / "file2.md"
    f2.write_text("This is file two with more words.", encoding="utf-8")

    from util.count_tokens import main

    with patch("util.count_tokens.fetch_token_count", return_value=12):
        ret = main(["-i", str(f1), str(f2), "--api-key", "test-key"])
        assert ret == 0
        captured = capsys.readouterr().out
        assert "TOKEN COUNT VISUALIZER" in captured
        assert "Token Distribution:" in captured
        assert "TOTAL" in captured


def test_main_cli_json_output(tmp_path, capsys):
    import json
    f1 = tmp_path / "file1.md"
    f1.write_text("JSON test file.", encoding="utf-8")

    from util.count_tokens import main

    with patch("util.count_tokens.fetch_token_count", return_value=5):
        ret = main(["-i", str(f1), "--api-key", "test-key", "--json"])
        assert ret == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert data["summary"]["total_tokens"] == 5
        assert len(data["files"]) == 1
        assert data["files"][0]["tokens"] == 5


def test_main_cli_missing_files(capsys):
    from util.count_tokens import main

    ret = main([])
    assert ret == 1
    err = capsys.readouterr().err
    assert "No input files provided" in err

