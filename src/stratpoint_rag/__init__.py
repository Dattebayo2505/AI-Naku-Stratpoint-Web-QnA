"""Stratpoint RAG chatbot.

Consumes the Markdown corpus produced by the sibling ``stratpoint_crawl``
package (maintained and run separately) via ``data/pages/`` + ``data/index.jsonl``.

Component packages:

- ``rag``            -- chunking, embeddings, vector store, retrieval (planned)
- ``prompts``        -- prompt engineering: system prompts, few-shot, CoT templates (planned)
- ``disambiguation`` -- ambiguous-input detection / intent clarification before tool calls (planned)
- ``guardrails``     -- input/output guardrails (planned)
- ``agent``          -- ReAct agent orchestrating retrieval and tools (planned)
- ``api``            -- HTTP API endpoint exposing the chatbot (planned)
- ``ui``             -- Streamlit chat UI (planned)
- ``evaluation``     -- retrieval/answer quality evaluation (planned)
"""
