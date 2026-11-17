"""Streamlit entry point for the local UI (Task 2.9b).

    uv run streamlit run app/ui/streamlit_app.py

Streamlit runs this file as a script, so the project root is put on ``sys.path`` first (the
``app`` package must resolve to ``<root>/app``, not to anything next to this file). Everything
else lives in ``app.ui.views``.
"""

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.ui.views import main  # noqa: E402

main()
