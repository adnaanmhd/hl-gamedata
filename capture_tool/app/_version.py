"""Single source of truth for the app version.

Kept as its own tiny, dependency-free module (not defined in
session_engine.py or main.py) so it can be imported cheaply from
`main.py`'s logging setup — which runs deliberately BEFORE any heavy app
import, per main.py's own docstring — without pulling in PySide6/pynput/etc.

Bump this on every fix that lands. The whole point: `humyncapture.log`'s
very first lines now say exactly which version produced it, so "is this
exe actually running the latest fixes" is answerable by reading one line
instead of guessing from symptoms — several rounds of back-and-forth here
were spent on exactly that ambiguity.
"""
HUMYN_VERSION = "0.13.0"
