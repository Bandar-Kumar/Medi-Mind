"""Records layer: the ONLY database access path in MediMind (ADR-016).

Engines, sessions and (from Task 1.8) patient-scoped repositories live here. Code outside this
package must not open sessions or engines; tests/test_architecture.py enforces this.
"""
