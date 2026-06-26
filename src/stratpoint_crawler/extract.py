import hashlib
import re

from markdownify import markdownify as md
from selectolax.parser import HTMLParser

from .config import Settings
from .models import PageContent

_BLANKS = re.compile(r"\n{3,}")


def _title(tree: HTMLParser, settings: Settings) -> str:
    h1 = tree.css_first("h1")
    if h1 and h1.text(strip=True):
        return h1.text(strip=True)
    title_el = tree.css_first("title")
    raw = title_el.text(strip=True) if title_el else ""
    if raw.endswith(settings.title_suffix):
        raw = raw[: -len(settings.title_suffix)]
    return raw.strip()


def _main_html(tree: HTMLParser) -> str:
    """Pick the element holding the page's primary content.

    Priority:
      1. <main>             - the strongest semantic signal when present.
      2. single <article>   - blog templates with one canonical article.
      3. <body>             - chrome has already been stripped above; whatever
                              is left is the meaningful content. (Picking the
                              "largest div" sounds nice but on Divi-style
                              templates the topmost wrapper div always wins
                              and is identical to <body>.)
    """
    main = tree.css_first("main")
    if main is not None:
        return main.html or ""
    articles = tree.css("article")
    if len(articles) == 1:
        return articles[0].html or ""
    body = tree.css_first("body")
    return body.html if body else ""


def _normalize(markdown: str) -> str:
    return _BLANKS.sub("\n\n", markdown).strip()


def extract(html: str, *, url: str, settings: Settings) -> PageContent:
    tree = HTMLParser(html)
    title = _title(tree, settings)

    for selector in settings.chrome_selectors:
        for node in tree.css(selector):
            node.decompose()

    body_html = _main_html(tree)
    markdown = _normalize(md(body_html, heading_style="ATX"))
    text_len = len(markdown)
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

    return PageContent(
        url=url,
        title=title,
        markdown=markdown,
        text_len=text_len,
        content_hash=f"sha256:{digest}",
        thin=text_len < settings.thin_content_min,
    )
