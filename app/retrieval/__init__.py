"""Evidence retrieval (Milestone R1, ADR-035): pure logic only.

Chunking, query terms, rank fusion, page grouping, abstention and the advice guardrail. Nothing
here touches the database (``app/records/`` does) or loads a model at import time (``models.py``
imports ``transformers`` lazily, inside functions).
"""
