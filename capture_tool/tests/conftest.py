import sys
from pathlib import Path

# capture_tool/ itself (for `import app...`) and its parent repo root (for
# `import translator...`, used by app.core.finalize.pipeline).
_CAPTURE_TOOL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _CAPTURE_TOOL_ROOT.parent
for p in (str(_CAPTURE_TOOL_ROOT), str(_REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
