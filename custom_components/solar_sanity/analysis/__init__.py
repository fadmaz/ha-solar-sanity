"""Pure analysis engine for Solar Sanity.

Nothing in this package may import from ``homeassistant``. Purity is enforced by
``tests/analysis/test_invariants.py`` with an AST walk, not by convention.
"""

from analysis.engine import analyse

__all__ = ["analyse"]
