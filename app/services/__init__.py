"""Services (use cases): combine domain modules (e.g. ``app.ingestion``) with the records layer.

Layer rules (docs/ARCHITECTURE.md §2; tests/test_architecture.py): services may import domain
modules and the allow-listed names of ``app.records``; they never open sessions or engines, and
domain modules never import services.
"""
