from pydantic import BaseModel


class Settings(BaseModel):
    host: str = "stratpoint.com"
    sitemap_index_url: str = "https://stratpoint.com/sitemap_index.xml"

    concurrency: int = 4
    delay_min: float = 0.5
    delay_max: float = 1.5
    nav_timeout_ms: int = 30_000
    max_attempts: int = 3

    thin_content_min: int = 200
    title_suffix: str = " - Stratpoint"

    save_html: bool = False

    chrome_selectors: tuple[str, ...] = (
        "nav", "header", "footer", "script", "style", "noscript",
        "[class*='cookie']", "[id*='cookie']", "[class*='consent']",
        "[class*='share']", "[class*='social']", "form",
        # Divi (WordPress) related-posts widget — appears on every blog/case-study
        # page and would otherwise dominate the extracted body once the real main
        # content is identified.
        ".et_pb_posts", ".et_pb_post",
        # CookieYes consent banner (cky-*) — JS-injected, ends up in
        # page.content() even after best-effort dismissal.
        ".cky-modal", ".cky-consent-container", ".cky-overlay", ".cky-consent-bar",
    )

    consent_button_selectors: tuple[str, ...] = (
        ".cky-btn-accept",
        "#onetrust-accept-btn-handler",
        "button[aria-label*='accept' i]",
        "button:has-text('Accept')",
    )
