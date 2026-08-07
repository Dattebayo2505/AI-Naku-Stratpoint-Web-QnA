"""The hop-1 transcription prompt.

Hop 1 produces **ground truth** — a complete, verbatim transcription that
preserves the document's own visual hierarchy. It deliberately does not
reorganize content into Requirements / Constraints / Timeline sections; that is
hop 2's job, and splitting the inference across two models makes a wrong output
untraceable.

Two constraints in this prompt are counter-intuitive and will be re-litigated:

1. **Headings are ``###`` and deeper only.** Each page is an independent
   request with no cross-page context, so the model cannot know that page 3's
   "Requirements" and page 8's are peers. Left alone, a 20-slide deck yields
   twenty ``#`` headings at drifting depth. Python emits the ``##`` page
   wrapper; the model works below it, expressing hierarchy *within* its page
   only. Do not post-process heading levels across pages afterwards — that is
   guessing at document structure from twenty independent guesses.

2. **Anti-summarization language is load-bearing, not boilerplate.** Under load
   this model's failure mode is summarizing instead of transcribing: the same
   invoice page that transcribed perfectly at 1120x1456 dropped its entire
   Overview body and only summarized at 2240x2912. Every dropped clause is a
   lost requirement.

Observed behaviour on the live endpoint, after tuning (recorded so the next
person does not repeat the search):

- Bordered tables transcribe **accurately** — headers, every row, every cell.
  This was the predicted top error source and is currently the strongest part.
- Diagrams yield box names and connections in ``A -> B`` form, but the
  connections are only **approximately** right: on a four-box architecture
  drawing it produced one chain where the source had a branch. Treat figure
  blocks as a lead, not as ground truth.
- The model still sometimes appends a redundant ``**Figure:**`` block
  describing a table it has already transcribed correctly. That is noise, not
  data loss, and every attempt to suppress it with a prohibition also
  suppressed figure blocks on real diagrams — which is the far worse trade.

Two things do NOT belong in this prompt, because they were fixed structurally:

- The instructions go in a **system** message (see ``nim.py``). Sent in the same
  user turn as the image, the model transcribed the page and then continued
  straight into "### Rules" plus every bullet of this prompt, verbatim, as
  though it were printed on the page.
- Negative framing of the form "X is never a figure" reliably kills figure
  output altogether on this model. Prefer positive, concrete cues.
"""

from __future__ import annotations

__all__ = ["TRANSCRIPTION_PROMPT"]


TRANSCRIPTION_PROMPT = """\
You are transcribing one page of a client document. Reproduce it completely and \
exactly as Markdown.

Rules:
- Transcribe EVERY word of visible text. Do not summarize, shorten, paraphrase, \
or skip anything, however repetitive or unimportant it looks.
- Do not add commentary. Never write "This slide shows...", "The page contains...", \
or any description of the page as an object.
- Do not interpret. No "this implies", no inferred requirements, no conclusions \
the page does not state itself.
- Reproduce tables as Markdown tables, preserving every row, column, and cell \
value exactly.
- Preserve lists as Markdown lists and keep their original order and nesting.
- Boxes joined by lines or arrows are a DIAGRAM, not a table and not headings. \
Whenever you see one — an architecture drawing, a flowchart, a chart, a \
screenshot, a photo — transcribe the page's ordinary text as usual, then add \
one blockquote for the drawing:
  > **Figure:** <what it depicts. Name every box, then state each connection as \
"A -> B". The arrows carry the meaning; never omit them.>
- A grid of ruled cells is a TABLE. Transcribe it as a Markdown table and do not \
also describe it as a figure.
- A page made only of text, headings, lists, and tables has no figure at all. End \
your reply after its last line of text; say nothing about the page's layout, \
background, or colours.
- Use heading levels ### or deeper ONLY. Never write # or ## headings. Use them \
to reflect the relative hierarchy visible on THIS page.
- Output only the transcription itself. No preamble, no closing remarks, no code \
fence around the whole answer.

Only if the page is entirely empty — no text, no figures, nothing to transcribe \
— your whole reply must be exactly: (blank page)
Otherwise never write those words anywhere in your reply."""
