# Project Brief

## 1. Vision

MediMind is an **evidence-linked personal medical record intelligence platform**. It turns fragmented medical documents into structured, searchable, chronological health records. Every fact links back to the document, page and excerpt it came from.

It is an **information-management prototype**. It is not a diagnostic, prescribing or clinical decision-support system.

The portfolio goal is a system whose every claim can be traced, measured and defended. A collection of AI frameworks is not the goal.

## 2. Problem

Medical information is spread across lab reports, prescriptions, discharge summaries, consultation notes and diagnostic reports. These come from different providers and formats, over many years. Patients and caregivers struggle to answer basic questions:

- "How has my HbA1c changed?"
- "What medications were documented at my last discharge?"
- "Do my records disagree about my allergies?"

## 3. Users

- **Patients:** manage their own records.
- **Caregivers:** manage records for one or more family members, each as a separate patient profile.

The prototype is single-user and runs locally (ADR-013, Accepted).

**Initial region:** India, English-only documents, DD/MM/YYYY dates (ADR-025, accepted). India is also the initial intended privacy and data-protection jurisdiction. MediMind makes no claim of legal or regulatory compliance. Regional settings are configurable so other regions can be added later.

## 4. Capabilities

| Capability | Summary |
|---|---|
| A. Document intelligence | PDF and image upload, Docling parsing, OCR fallback, classification, page provenance, duplicate and revision detection, patient identity check |
| B. Structured clinical extraction | Lab observations, medications, allergies, follow-up instructions and events, using Pydantic schemas, a grounding check, normalization and a review status |
| C. Longitudinal intelligence | Timeline, lab trends, comparisons and conflict flags, computed deterministically in SQL and Python |
| D. Hybrid RAG | Query routing, SQL tools, dense and lexical retrieval, RRF, reranking, cited answers and abstention |
| E. Record comparison | Changed, missing and new observations; conflicting documented medications; evidence-backed summaries |
| F. Agentic workflows | LangGraph appointment preparation with retries and human review. Record analysis is deferred (ADR-012). |

These are supported by evaluation, security and reliability engineering throughout.

## 5. Scope

### MVP (end of Phase 5)
- Document types: **lab reports** first, then prescriptions and discharge summaries (Phase 6).
- Pages: Overview, Documents, Document detail and review, Timeline, Lab trends, Copilot.
- Copilot answers come from the records only (ADR-022, Accepted).

### Later
- Document comparison and appointment preparation.
- Consultation notes and the text of imaging reports.
- Local-only LLM mode.

### Out of scope
- Diagnosis, prescribing, treatment recommendations and medication-change advice.
- Handwritten prescriptions, and imaging pixel data (MediMind reads only the text of imaging reports).
- Non-English documents, including regional-language and mixed-language reports (ADR-025).
- Multi-user authentication, encryption at rest and production deployment.
- Real patient data.

## 6. Safety boundaries

MediMind must not:
- diagnose,
- prescribe,
- recommend medication changes,
- make autonomous clinical decisions,
- invent missing information,
- silently resolve conflicting records,
- present unverified extraction as fact.

When a user asks for advice, MediMind gives a fixed response that redirects them to a clinician and offers to show what their records document.

## 7. Data strategy

- **Development and demo:** Fictional synthetic documents generated from structured ground truth (ADR-010). Synthea is a candidate source of patient histories; it needs Java and the licence must be verified.
- **Excluded public datasets:**
  - **MIMIC-IV / MIMIC-IV-Note**: credentialed access, a data-use agreement and restrictions on sending the data to third-party LLM APIs.
  - **MedQuAD**: general consumer health Q&A, not personal records. At most, we might use it to test out-of-scope refusals, after checking its licence.
- **Evaluation:**
  - Development and held-out splits, separated by patient. The held-out set is frozen before tuning.
  - A small hand-annotated realism set.
- Real patient records are never sent to external AI services.

## 8. Success criteria

Results will be measured and reported. No targets are claimed in advance; they are set in Phase 1 once the first baselines exist.

1. Every stored fact has document and page provenance. Facts that fail the grounding check are routed to review.
2. Extraction field-level F1, retrieval Recall@k and MRR, citation precision and abstention precision and recall are reported on a held-out set.
3. Lab trends exactly match the ground truth. They are deterministic.
4. Conflicts are flagged and never merged silently.
5. Advice-seeking and prompt-injection test prompts are handled safely.
6. Patient isolation is proven by automated tests.

## 9. Non-functional targets (initial, to be confirmed)

| Target | Initial value |
|---|---|
| Development machine | Windows 11, 16 GB RAM, CPU only |
| Maximum upload size | 20 MB |
| Maximum pages per document | 30 |
| Embeddings and reranking | Run locally on CPU (BGE-small, MiniLM cross-encoder) |
| Heavier LLM tasks | Provider chosen by measured comparison at the start of Phase 3; **target: zero spend (free tier or local model)** (ADR-024) |
| Cost and latency | Measured and logged per document and per question; budgets set after the Phase 3 baseline |
