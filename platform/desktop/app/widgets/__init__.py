"""Shared widgets. Pages use these instead of styling their own."""

from .controls import Button, Segmented, Toggle
from .marks import FlagValue, Pill, SimChip, StatusDot
from .panel import KpiCell, KpiStrip, Panel
from .sparkline import Sparkline
from .table import CellDelegate, DataTable

__all__ = [
    "Button", "CellDelegate", "DataTable", "FlagValue", "KpiCell", "KpiStrip", "Panel", "Pill", "Segmented",
    "SimChip", "Sparkline", "StatusDot", "Toggle",
]
