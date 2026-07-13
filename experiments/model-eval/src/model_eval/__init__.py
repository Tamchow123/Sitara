"""Sitara Phase 2 image-model feasibility evaluation.

Standalone experiment package. It must never import future Django
application code, and no module in it may contact a paid provider unless
every live-run gate is satisfied (see model_eval.replicate_client and
model_eval.runner).
"""

__version__ = "0.1.0"
