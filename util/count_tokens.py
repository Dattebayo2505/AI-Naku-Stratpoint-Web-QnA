#!/usr/bin/env python3
"""Visualize token count from file inputs using NVIDIA NIM API.

Usage:
    python count_tokens.py -i file1.md file2.md
    python count_tokens.py -i data/pages/about-us.md data/pages/careers.md
    python count_tokens.py --help
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from dotenv import load_dotenv
import httpx

load_dotenv()

DEFAULT_MODEL = "meta/llama-3.1-8b-instruct"
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_TIMEOUT = 60.0


@dataclass
class FileMetrics:
    file_path: str
    characters: int = 0
    words: int = 0
    lines: int = 0
    tokens: int = 0
    tokens_per_word: float = 0.0
    share_pct: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "file": self.file_path,
            "lines": self.lines,
            "words": self.words,
            "characters": self.characters,
            "tokens": self.tokens,
            "tokens_per_word": round(self.tokens_per_word, 2),
            "share_pct": round(self.share_pct, 1),
            "error": self.error,
        }


@dataclass
class TokenSummary:
    metrics: list[FileMetrics] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0
    total_words: int = 0
    total_chars: int = 0
    total_tokens: int = 0
    avg_tokens_per_word: float = 0.0
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_BASE_URL

    @classmethod
    def from_metrics(
        cls,
        metrics: list[FileMetrics],
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_BASE_URL,
    ) -> TokenSummary:
        summary = cls(metrics=metrics, model=model, endpoint=endpoint)
        summary.total_files = len(metrics)
        summary.total_lines = sum(m.lines for m in metrics)
        summary.total_words = sum(m.words for m in metrics)
        summary.total_chars = sum(m.characters for m in metrics)
        summary.total_tokens = sum(m.tokens for m in metrics)

        if summary.total_words > 0:
            summary.avg_tokens_per_word = summary.total_tokens / summary.total_words

        for m in metrics:
            if summary.total_tokens > 0:
                m.share_pct = (m.tokens / summary.total_tokens) * 100.0
            else:
                m.share_pct = 0.0

        return summary

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "endpoint": self.endpoint,
            "summary": {
                "total_files": self.total_files,
                "total_lines": self.total_lines,
                "total_words": self.total_words,
                "total_characters": self.total_chars,
                "total_tokens": self.total_tokens,
                "avg_tokens_per_word": round(self.avg_tokens_per_word, 2),
            },
            "files": [m.as_dict() for m in self.metrics],
        }


def compute_metrics(file_path: str, text: str, prompt_tokens: int, error: str | None = None) -> FileMetrics:
    lines = len(text.splitlines()) if text else 0
    words = len(text.split()) if text else 0
    characters = len(text)
    tok_per_word = (prompt_tokens / words) if words > 0 else 0.0

    return FileMetrics(
        file_path=file_path,
        characters=characters,
        words=words,
        lines=lines,
        tokens=prompt_tokens,
        tokens_per_word=tok_per_word,
        error=error,
    )


def fetch_token_count(
    text: str,
    model: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> int:
    """Query NVIDIA NIM chat/completions API with max_tokens=1 to extract prompt_tokens."""
    if not text.strip():
        return 0

    if not api_key:
        raise ValueError("NVIDIA_API_KEY is not set. Please set it in .env or pass --api-key.")

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 1,
        "temperature": 0.0,
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        return usage.get("prompt_tokens", 0)


def analyze_file(
    file_path: str,
    model: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> FileMetrics:
    path = Path(file_path)
    if not path.exists():
        return FileMetrics(file_path=file_path, error=f"File not found: {file_path}")
    if not path.is_file():
        return FileMetrics(file_path=file_path, error=f"Not a file: {file_path}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return FileMetrics(file_path=file_path, error=f"Read error: {e}")

    try:
        tokens = fetch_token_count(content, model=model, api_key=api_key, base_url=base_url, timeout=timeout)
        return compute_metrics(file_path, content, prompt_tokens=tokens)
    except Exception as e:
        # Fall back to reporting character/word count even if API failed
        lines = len(content.splitlines())
        words = len(content.split())
        return FileMetrics(
            file_path=file_path,
            characters=len(content),
            words=words,
            lines=lines,
            tokens=0,
            tokens_per_word=0.0,
            error=str(e),
        )


def format_ascii_table(summary: TokenSummary, model_name: str | None = None) -> str:
    model = model_name or summary.model
    header_bar = "=" * 88
    sub_bar = "-" * 88

    lines = [
        header_bar,
        "                               TOKEN COUNT VISUALIZER",
        header_bar,
        f"Model:    {model}",
        f"Endpoint: {summary.endpoint}",
        f"Files:    {summary.total_files}",
        sub_bar,
        "",
        "+--------------------------------------+--------+--------+---------+---------+----------+-------+",
        "| File                                 |  Lines |  Words |   Chars |  Tokens | Tok/Word | Share |",
        "+--------------------------------------+--------+--------+---------+---------+----------+-------+",
    ]

    for m in summary.metrics:
        name = m.file_path
        if len(name) > 36:
            name = "..." + name[-33:]

        if m.error:
            lines.append(
                f"| {name:<36} | {m.lines:>6,d} | {m.words:>6,d} | {m.characters:>7,d} | [ERROR] |   --     |  --   |"
            )
        else:
            lines.append(
                f"| {name:<36} | {m.lines:>6,d} | {m.words:>6,d} | {m.characters:>7,d} | {m.tokens:>7,d} | {m.tokens_per_word:>8.2f} | {m.share_pct:>4.1f}% |"
            )

    lines.extend([
        "+--------------------------------------+--------+--------+---------+---------+----------+-------+",
        f"| TOTAL                                | {summary.total_lines:>6,d} | {summary.total_words:>6,d} | {summary.total_chars:>7,d} | {summary.total_tokens:>7,d} | {summary.avg_tokens_per_word:>8.2f} | 100%  |",
        "+--------------------------------------+--------+--------+---------+---------+----------+-------+",
    ])

    return "\n".join(lines)


def format_ascii_bar_chart(summary: TokenSummary, width: int = 36) -> str:
    if not summary.metrics:
        return ""

    lines = ["\nToken Distribution:"]
    for m in summary.metrics:
        name = m.file_path
        if len(name) > 28:
            name = "..." + name[-25:]

        if m.error:
            lines.append(f"{name:<28}  [ ERROR: {m.error} ]")
            continue

        pct = m.share_pct
        filled = int(round((pct / 100.0) * width))
        filled = max(0, min(width, filled))
        empty = width - filled
        bar = "#" * filled + "." * empty

        lines.append(f"{name:<28}  [{bar}]  {pct:>5.1f}% ({m.tokens:,d} tokens)")

    return "\n".join(lines)


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize token count from file inputs using NVIDIA NIM API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        "--files",
        dest="files",
        action="extend",
        nargs="+",
        default=[],
        help="Input files to analyze (can be passed multiple times or space-separated).",
    )
    parser.add_argument(
        "positional_files",
        nargs="*",
        default=[],
        help="Additional input files passed positionally.",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=os.getenv("LLM_MODEL") or DEFAULT_MODEL,
        help="NVIDIA NIM model name (or set LLM_MODEL in .env).",
    )
    parser.add_argument(
        "-k",
        "--api-key",
        default=os.getenv("NVIDIA_API_KEY") or "",
        help="NVIDIA API Key (or set NVIDIA_API_KEY in .env).",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NVIDIA_BASE_URL") or DEFAULT_BASE_URL,
        help="NVIDIA NIM base URL (or set NVIDIA_BASE_URL in .env).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="API request timeout in seconds.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON.",
    )
    parser.add_argument(
        "--no-bar",
        action="store_true",
        help="Hide the ASCII distribution bar chart.",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=4,
        help="Concurrent API workers for multi-file processing.",
    )

    parsed = parser.parse_args(args)
    # Merge -i files and positional files
    all_files = []
    if parsed.files:
        all_files.extend(parsed.files)
    if parsed.positional_files:
        all_files.extend(parsed.positional_files)

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for f in all_files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    parsed.files = deduped
    return parsed


def main(args: Sequence[str] | None = None) -> int:
    parsed = parse_args(args)
    if not parsed.files:
        print("Error: No input files provided. Use -i file1.md file2.md or see --help.", file=sys.stderr)
        return 1

    if not parsed.api_key:
        print(
            "Error: NVIDIA_API_KEY is not set.\n"
            "Please set NVIDIA_API_KEY in your .env file or pass --api-key <KEY>.",
            file=sys.stderr,
        )
        return 1

    # Process files concurrently
    metrics_list: list[FileMetrics] = []
    with ThreadPoolExecutor(max_workers=parsed.concurrency) as executor:
        future_to_file = {
            executor.submit(
                analyze_file,
                f,
                model=parsed.model,
                api_key=parsed.api_key,
                base_url=parsed.base_url,
                timeout=parsed.timeout,
            ): f
            for f in parsed.files
        }
        # Maintain input order
        results = {}
        for future in as_completed(future_to_file):
            f = future_to_file[future]
            try:
                results[f] = future.result()
            except Exception as e:
                results[f] = FileMetrics(file_path=f, error=str(e))

    for f in parsed.files:
        if f in results:
            metrics_list.append(results[f])

    summary = TokenSummary.from_metrics(metrics_list, model=parsed.model, endpoint=parsed.base_url)

    if parsed.json:
        print(json.dumps(summary.as_dict(), indent=2))
    else:
        table = format_ascii_table(summary, model_name=parsed.model)
        print(table)
        if not parsed.no_bar and len(summary.metrics) > 0:
            chart = format_ascii_bar_chart(summary)
            print(chart)
            print("=" * 88)

    # Return non-zero if any file failed
    has_errors = any(m.error is not None for m in summary.metrics)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
