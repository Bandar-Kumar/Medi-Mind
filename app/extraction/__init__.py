"""Structured clinical extraction (Phase 3; docs/PHASE_3_PLAN.md).

Pure domain logic: no database, no services, no Docling or torch imports. Services load the
verified IR and the stored page text, call the extractor, and hand its output to the records layer.
"""
