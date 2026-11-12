# MediMind — Claude Code Project Instructions

## Project identity

MediMind is an evidence-linked personal medical record intelligence platform.

Its purpose is to transform fragmented medical documents into structured, searchable and chronological health records.

Primary proposed users: Patients and caregivers.

## Core capabilities

1. Medical document processing and classification.
2. Structured clinical information extraction.
3. Longitudinal medical record intelligence.
4. Evidence-grounded hybrid RAG.
5. Medical document comparison and discrepancy detection.
6. LangGraph-based medical information workflows.
7. Evaluation, security and reliability engineering.

## Product boundaries

MediMind is a healthcare information-management prototype.

It must not:
- Diagnose medical conditions.
- Prescribe medicines.
- Recommend medication changes.
- Make autonomous clinical decisions.
- Invent missing medical information.
- Silently resolve conflicting records.
- Present unverified extraction as fact.

Use fictional synthetic medical records for demonstrations.

## Architecture principles

- Start with a modular monolith.
- Use Python for application logic.
- Use Streamlit initially for the interface.
- Use PostgreSQL and pgvector for structured records and vector retrieval.
- Use Docling for document processing.
- Use Pydantic for structured extraction and validation.
- Use LangChain for LLM and retrieval integrations.
- Introduce LangGraph only for workflows requiring orchestration, branching and human review.
- Use deterministic Python and SQL for numerical comparisons.
- Keep the LLM provider replaceable.

Do not add infrastructure or dependencies without a clear technical reason.

## Evidence and reliability

- Preserve original extracted values.
- Preserve document IDs, page numbers and source excerpts.
- Distinguish facts, inferences and unknown information.
- Validate units, dates and reference ranges.
- Flag ambiguous or conflicting information.
- Cite original evidence for factual answers.
- Abstain when evidence is insufficient.

## Engineering rules

- Inspect existing files before changing them.
- Plan before significant implementation.
- Implement one task at a time.
- Write modular, typed and maintainable Python.
- Add tests for meaningful components.
- Handle exceptions and invalid inputs.
- Explain important technical decisions.
- Do not fabricate test results or performance metrics.
- Do not silently change approved architecture.
- Do not perform destructive operations without approval.
- Do not commit or push changes without user approval.
- Update documentation and progress after completed tasks.

## Learning requirements

The project owner is learning AI engineering while building MediMind.

Explain important architecture, code, database queries, retrieval logic and engineering trade-offs.

Do not generate large unexplained codebases.

## Documentation

Treat these files as project sources of truth:

- docs/PROJECT_BRIEF.md
- docs/ROADMAP.md
- docs/ARCHITECTURE.md
- docs/USER_FLOWS.md
- docs/DATABASE_DESIGN.md
- docs/DECISIONS.md
- docs/IMPLEMENTATION_PLAN.md
- docs/PROGRESS.md

These documents are written at design level in Phase 0 and refined as each phase begins. Code directories are still created only when their phase starts. Decisions in docs/DECISIONS.md marked "Proposed" are not final.

If information conflicts, identify the conflict and ask for clarification.

Do not assume an unapproved proposal is a finalized decision.