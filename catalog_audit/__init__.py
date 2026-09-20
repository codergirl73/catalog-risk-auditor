"""Catalog Risk Auditor.

Audits a music catalog before acquisition: scores every asset through the
HumanStandard detection API, sorts assets into clean, contested and suspect,
joins the result to reported revenue, and produces an escrow recommendation
plus a human review queue.
"""

__version__ = "0.1.0"
