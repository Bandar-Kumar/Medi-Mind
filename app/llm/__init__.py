"""LLM provider interface and adapters (Phase 5; ADR-011, ADR-024, ADR-040).

Knows nothing about patients, documents or medicine: it sends the prompts it is given and returns
text plus usage. Adapters are imported lazily by the code that needs them.
"""
