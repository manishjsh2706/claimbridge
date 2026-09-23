"""
Operational scripts for ClaimBridge.

These are entry points you run by hand or from CI, not application code.

    python -m src.claimbridge.scripts.seed_weaviate

Kept inside the package (rather than a top-level scripts/ folder) so they can
use the same relative imports and config as the app itself -- no sys.path
juggling, no duplicated connection logic.
"""
