from pydantic import BaseModel


class PageRef(BaseModel):
    url: str
    lastmod: str | None = None


class PageContent(BaseModel):
    url: str
    title: str
    markdown: str
    text_len: int
    content_hash: str
    thin: bool = False


class PageResult(BaseModel):
    url: str
    slug: str
    status: str
    error: str | None = None
    content: PageContent | None = None
    lastmod: str | None = None


class CrawlSummary(BaseModel):
    results: list[PageResult] = []
    skipped: int = 0
    removed: list[str] = []
