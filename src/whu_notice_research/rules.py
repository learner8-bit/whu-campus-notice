"""Stable public entry point for the frozen first-generation ruleset.

Do not add site-specific exceptions here. Cross-site evaluation imports the
frozen implementation from :mod:`rules_v1` through this compatibility module.
"""

from .rules_v1 import RULESET_VERSION, RuleDecision, decide

__all__ = ["RULESET_VERSION", "RuleDecision", "decide"]
