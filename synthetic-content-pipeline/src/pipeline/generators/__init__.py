"""Content generators — ContentSpec → 자연어 후보 (codex bridge 경유)."""
from pipeline.generators.base import BaseGenerator, Candidate
from pipeline.generators.detail import SpotDetailGenerator
from pipeline.generators.feed import FeedGenerator
from pipeline.generators.messages import MessagesGenerator
from pipeline.generators.plan import SpotPlanGenerator
from pipeline.generators.review import ReviewGenerator

__all__ = [
    "BaseGenerator",
    "Candidate",
    "FeedGenerator",
    "SpotDetailGenerator",
    "SpotPlanGenerator",
    "MessagesGenerator",
    "ReviewGenerator",
]
