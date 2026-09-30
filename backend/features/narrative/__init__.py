"""Shadow narrative metadata, labeling, linking, and heat features."""

from .heat import get_narrative_labels_for_ticker
from .labeler import NarrativeLabel, label_text
from .ticker_linker import TickerLink, TickerLinker

__all__ = [
    "NarrativeLabel",
    "TickerLink",
    "TickerLinker",
    "get_narrative_labels_for_ticker",
    "label_text",
]
