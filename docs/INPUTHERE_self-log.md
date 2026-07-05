# Self Log

> Your own running log. **Claude does not edit this file — it's yours.**
> Rename the `INPUTHERE` prefix when you like (e.g. to your name or initials).
> Format is freeform; the example below shows one style. Newest entries at the top.

---

[2026-07-15] Made an agentic API design spec for the ReAct agent using NVIDIA NIM integrated langchain development. Two tools were made, `search_stratpoint` and `find_resource`. The `find_resource` tool finds files embedded in the RAG corpus and returns them to the agent for the user. This does not however cover cases where files are removed from the actual links themselves. 
To implement:
- Possible file downloading for user.  

[2026-06-26] Applied incremental mode to the crawler (Replacing old scraped web pages with more recent if applicable). Not thoroughly tested yet.


[2026-06-13] Extracted 371 pages from stratpoint. Need to decide what to do with the
~7-8 thin_content pages (e-book gate forms / QA test stubs) — exclude from RAG corpus or keep?

