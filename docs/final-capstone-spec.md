# Final Capstone — Project Specification

**Introduction to Agentic AI (STAI100)** · Due Week 14 · Teams of 3–4 students

> **Transcribed from the official PDF** (`STAI100 AI Naku (Stratpoint Chatbot)-1.pdf`,
> 11 pages). **The PDF is authoritative** — if this file and the PDF disagree,
> the PDF wins and this file is wrong.
>
> This is the **graded** specification. It is a different document from the
> team's own planning doc (the 6-page `STAI100 AI Naku (Stratpoint Chatbot).pdf`,
> which carries the member assignments and per-member task breakdowns). Where
> the two disagree about what is *required*, this one governs; the team doc
> describes work the team chose to do, not work the rubric checks.

## Key changes & reminders from the Midterm Capstone

- Pivot to **Proof-of-Concept (PoC)** mode: narrow scope, make the agent
  smarter, and do a Review of Related Literature (RRL) on available CV/DS models
- **CV or DS domain model integration is now mandatory** as the core use case
  (Component #14)
- Component ownership: same per-member model as the Midterm — each member owns
  at least 2 of the 14 components (6 for a 3-person team, 8 for a 4-person
  team); Component #14 is mandatory for the team
- **Slide deck format changes:** LLM (+ parameter size) in the title slide, agent
  name/tagline in the footer, navigation tabs/breadcrumbs, and the live demo
  moves to the *end* of the deck
- New presentation **MUST HAVEs:** team member contributions; Unit of
  Measurement (UoM) discipline; a clear value proposition; RRL / model-selection
  reasoning
- Presentation length: **12–15 min per team + 3 min Q&A**
- Grading rubric weights are unchanged (30/25/20/15/10), but what each criterion
  looks for has shifted toward **CV/DS integration**, **business use case**, and
  **deeper agent evals**

## 1. Overview

The Final Capstone is the culminating project of the course. Where the Midterm
asked you to integrate agentic components from Weeks 1–7 into a coherent,
deployable application, the Final asks you to take that system further: refine it
into a focused proof of concept, and integrate a **Computer Vision (CV) or Data
Science (DS) model as a core part of the solution — not a decorative add-on.**

Building on the Midterm project is encouraged but not required. Either way, the
Final should reflect a **narrower, sharper business use case** than the Midterm,
backed by a short review of the state of the art for your task.

**What you will build**

- A refined agentic AI application solving a real business problem, with a CV or
  DS model as a core, non-decorative component
- At least 2 components per team member from across the course (6 for a
  3-person team, 8 for a 4-person team) — Component #14 is mandatory
- Accessible via a web UI and an API endpoint
- Deployed with basic LLMOps monitoring
- Documented with a technical write-up and a clean code repository
- Backed by a more rigorous **evaluation suite** than the Midterm

## 2. Learning objectives

- Integrate a CV or DS model as a first-class component of an agentic system,
  not just a text-only pipeline
- Conduct an appropriate RRL to select an appropriate CV/DS approach
- Refine a business use case to a narrow, defensible scope, and articulate why an
  agentic (rather than single-prompt) approach is warranted
- Design and run a more rigorous evaluation suite, building on the
  unit/trajectory/end-to-end/LLM-as-judge techniques from Week 11
- Communicate a clear, quantified value proposition to a non-specialist
  audience, with disciplined units of measurement
- Present technical work with full team accountability for each component

## 3. Project requirements

### 3.1 Technical requirements

Carried over from the Midterm — unchanged, plus the CV/DS requirement:

- Working, end-to-end agentic AI application
- Accessible via a web UI (e.g. Streamlit, Gradio)
- Exposes an API endpoint (REST)
- Deployed with basic LLMOps monitoring (e.g. **MLflow**)
- Containerized with a Dockerfile and documented build/run instructions

**New for the Final:**

- Must integrate a **CV or DS model as a core use case** (Component #14)
- Must integrate **at least 2 of the 14 components per team member** (6 for a
  3-person team, 8 for a 4-person team), including the mandatory CV/DS
  integration
- Should reflect a narrowed/refined scope relative to a typical Midterm project

### 3.2 Team requirements

3–4 students per team; teams self-formed by Week 6. **Each member must own and be
able to explain at least 2 of the components they contributed.** All members
participate in the live presentation.

### 3.3 Deliverables

| Deliverable | Details |
|---|---|
| **Live Presentation** | 12–15 minutes + 3 min Q&A per team; slides required |
| **Technical Write-up** | Business case, RRL/model selection, methodology, architecture, experiments, retrospective |
| **Source Code Repository** | GitHub (or equivalent .zip) with README, Dockerfile, and inline documentation |
| **Working Demo** | Live, accessible demo during the presentation (no pre-recorded video substitutes — except as backups) |

## 4. Component checklist

Each member must own and demonstrate at least 2 of the 14 below. Component #14 is
mandatory for the team.

| # | Component | Description | Primary Week |
|---|---|---|---|
| 1 | Prompt Engineering | Design and iterate on system prompts; few-shot, chain-of-thought, structured prompt patterns | 1 |
| 2 | Disambiguation | Detect ambiguous inputs, classify intent, clarify before acting | 2 |
| 3 | RAG | Retrieve context from a vector, SQL, or graph store; ground responses in retrieved data | 3 |
| 4 | Memory | Short-term and/or long-term memory across conversations | 4 |
| 5 | Guardrails | Input/output validation, topic filtering, PII redaction, safety checks | 4 |
| 6 | Simple Chat UI | Functional conversational interface (e.g. Streamlit, Gradio) | 5 |
| 7 | API Endpoint Deployment | Expose the agent via a REST API endpoint | 5 |
| 8 | LLMOps (monitoring/tracing) | Log traces, latency, token usage, and errors using an observability tool | 5, 13 |
| 9 | ReAct / Tool Use | Reasoning + acting loop; the agent plans and executes steps iteratively, calling external tools | 6 |
| 10 | SQL Agent / Planning-Critique | Natural-language-to-SQL querying and/or a plan → execute → self-critique loop | 7 |
| 11 | Multi-Agent Orchestration | Multiple specialized agents collaborating sequentially, in parallel, or hierarchically (e.g. LangGraph, CrewAI) | 10 |
| 12 | Advanced RAG | Hybrid search, reranking, query rewriting/decomposition, or agentic retrieval | 13 |
| 13 | **Evals** | **Unit, trajectory, and end-to-end evaluations, including an LLM-as-a-judge component** | 11 |
| 14 | **CV or DS Domain Integration ★ mandatory** | A CV or DS model wrapped as a callable tool inside the agent (e.g. object detection/OCR, EDA, forecasting, classification) | 12 |

> Note on #13: the graded requirement is *unit, trajectory, and end-to-end
> evaluations plus an LLM-as-a-judge component*. Named eval types beyond those
> (e.g. "brief extraction accuracy", "cost calculation correctness") come from
> the team planning doc, not from this specification.

## 5. Refining your use case: PoC pivot & RRL

**Pivot to PoC mode.** Make the agent smarter and narrow the scope. A good Final
does one thing well rather than many things adequately.

**Do your RRL.** Identify the state-of-the-art DS/CV models available for your
task. You don't need to understand the algorithm internals — but you should know
the available options, when to use one over another, and their expected
input/output.

**Refine the business use case.** Run the sanity check: *could ChatGPT, Claude, or
a Google search alone suffice for this?* If yes, the use case is not strong enough
to justify an agentic CV/DS solution — narrow it further, or add a component that
genuinely requires the agent (and the domain model) to be there.

## 6. Presentation structure & slide deck requirements

### 6.1 Presentation content requirements

- **Live demo** of the working system (must run live, not recorded) — placed at
  the **end** of the presentation
- **Architecture diagram** showing all integrated components
- **Eval results:** at minimum **3 quantitative metrics with interpretation**
- **Reasoning trace walkthrough:** demonstrate at least one full agent decision
  chain
- **Design decisions:** key architectural choices and trade-offs
- **Lessons learned** and known limitations

> A functional live demo is expected. A demo that fails to run during the
> presentation will affect the "Presentation Quality and Live Demo" criterion.
> Prepare a fallback (e.g. screen recording) and disclose it upfront.

### 6.2 Slide deck format changes (new for the Final)

- **Title slide:** include the LLM used; mention parameter size if open-source
  (e.g. 3B, 7B). Examples: *"Agent005: a research agent built on Gemini 3.1 Pro"*,
  *"Agent006: a presales agent powered by Llama-3.2-3B"*
- **Footer:** agent name & tagline on every slide
- **Navigation:** tabs/breadcrumbs so the audience can track where they are
- **Demo placement:** live demo at the **end** (the Midterm placed it mid-deck)

### 6.3 Presentation MUST HAVEs (new for the Final)

- **Team member contributions:** who worked on which module
- **Unit of Measurement (UoM):** review your UoM for any cost, savings, or effort
  figure. If a human doing a task costs XX pesos, qualify the time period — per
  hour? per week? how many hours in a month? Applies to costs, savings, and any
  measure of effort or work
- **Clear value proposition:** are you proposing something new/novel, or an
  improvement by a specific factor? (If the answer to both is no, there is no
  clear value.) Present a measure for the value you estimated — e.g. it saves 20
  hours a month, or a customer-experience improvement of 10x that brings 5 new
  customers a month, valued at ___ (cost of customer acquisition). You may ask an
  LLM to help articulate or measure value, but be ready to defend it. Intangibles
  are allowed if **explicitly flagged as intangible**
- **RRL:** not a separate written deliverable, but you must present it — briefly
  walk through the state-of-the-art options considered and justify your CV/DS
  model choice

## 7. Grading rubric

Weights are unchanged from the Midterm. What changed is what each criterion looks
for.

| Criterion | Weight | What's different for the Final |
|---|---|---|
| **Technical Depth and Correctness** | **30%** | Now includes correct integration of your **CV/DS** model as a first-class technical component, alongside RAG/memory/guardrails/tool use |
| **System Architecture and Design Quality** | **25%** | Architecture must clearly show where and how the **CV/DS** model plugs into the agent pipeline |
| **Eval Results and Reliability Demonstration** | **20%** | **Deeper evaluation** expected. The Midterm was largely graded on your own sample outputs and test cases; the Final looks for a more rigorous eval suite, building on the Week 11 material |
| **Presentation Quality and Live Demo** | **15%** | **Business use case** is now explicitly graded here — value proposition, UoM discipline, and team contributions must be clearly presented (see §6.3) |
| **Code Quality, Documentation, and README** | **10%** | Unchanged |

## 8. Course grading context

The Final Capstone contributes **40%** of the final course grade.

| Assessment | Weight | Description |
|---|---|---|
| Weekly Homework | 25% | Lab exercises submitted as Jupyter notebooks with documentation |
| Midterm Capstone (Week 9) | 30% | Working agentic system demonstrating components from Weeks 1–7 |
| **Final Capstone Project (Week 14)** | **40%** | End-to-end agentic solution with CV/DS model integration |
| Participation & Peer Review | 5% | In-class engagement, capstone dry-run feedback, Week 14 peer evaluations |

## 9. Choosing a good problem

| ✅ Good fits for agentic AI | ❌ Poor fits for agentic AI |
|---|---|
| Multi-step reasoning over external tools or APIs | Single-call Q&A; a well-crafted prompt would suffice |
| Processes unstructured data (PDFs, audio, images, web pages) | Pure CRUD apps using an LLM as a thin wrapper |
| Needs memory or context across a conversation | Tasks where deterministic code already wins |
| Real users with measurable success criteria | Problems with no ground truth or evaluation framework |
| Workflow currently done manually and repeatedly | Safety-critical workflows without a human-in-the-loop |

## 10. Project ideas

From the course outline: Smart Campus Assistant (CV), Product Review Analyzer
(DS), Study Buddy Bot (DS), Inventory Inspector (CV).

| 📷 Computer Vision track | 📊 Data Science track |
|---|---|
| Object detection and OCR pipelines | RAG-driven NLP pipelines |
| Multimodal agents (image + text) | Analytics and report automation |
| Document understanding (forms, receipts) | Tool-using research agents |
| Example: LLM + CV for license plate retrieval, object detection timestamps | Example: LLM + DS for forecasting, segmentation, or financial modeling |

Good pivots when extending a Midterm project: a supply chain agent that
incorporates a demand forecasting model; a hospital triage assistant that adds
medical image analysis; a customer segmentation tool that plugs in a
classification or clustering model.

## 11. Submission checklist

★ marks items new for the Final; carried-over items are unchanged.

- [ ] Working demo accessible via web UI and API endpoint
- [ ] ★ CV or DS model integrated as a core component and demonstrable live
- [ ] ★ At least 2 components per team member integrated and demonstrable (6 for
      a 3-person team, 8 for a 4-person team), including the mandatory CV/DS
      component
- [ ] ★ RRL / model-selection reasoning documented in the write-up and
      presentation
- [ ] LLMOps monitoring configured (traces, latency, token usage visible)
- [ ] Dockerfile builds and runs cleanly with a single command
- [ ] README includes: project overview, setup instructions, architecture
      diagram, and component ownership table
- [ ] ★ Slide deck follows the updated format: LLM (+ params) in title slide,
      agent name/tagline footer, nav tabs, demo at the end
- [ ] ★ Value proposition and Unit of Measurement (UoM) clearly and defensibly
      stated
- [ ] ★ Team member contributions slide included
- [ ] Technical write-up submitted as PDF or markdown
- [ ] Presentation slides finalized and submitted
- [ ] All team members prepared to answer questions on their contributed
      components
