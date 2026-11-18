"""Phase 6 clinical-document intelligence (docs/PHASE_6_PLAN.md; ADR-042).

Pure and deterministic: no database, no files except the versioned reference CSV, no models, no
clock. Kept outside ``app/extraction`` so the frozen Phase 3 extraction source digest stays valid;
the Phase 3 date, span and grounding helpers are imported, never changed.
"""
