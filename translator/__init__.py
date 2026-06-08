"""HumynLabs → Odyssey Game Data Capture translator (spec v1).

Turns raw HumynCapture bundles into the canonical delivery format, correctly
and reproducibly. See README.md for the guideline checklist this enforces.
"""
from .translate import translate_bundle, reprocess_session
from .qa import check_session, QAResult

__all__ = ["translate_bundle", "reprocess_session", "check_session", "QAResult"]
__version__ = "1.0.0"
