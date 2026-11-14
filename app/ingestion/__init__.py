"""Document ingestion building blocks (Phase 2): validation, hashing, storage and the parse IR.

Layer rule (enforced by tests/test_architecture.py): nothing in this package touches the
database. It never imports SQLAlchemy, psycopg, Alembic, pgvector, ``app.records`` or
``app.services``. The ingestion service (``app/services/``) combines these pieces with the
patient-scoped repositories. Docling and torch are only ever imported lazily, inside the parser.
"""
