"""Map ATS names from companies.yaml to collector classes."""
from __future__ import annotations

from .ashby import AshbyCollector
from .base import Collector
from .comeet import ComeetCollector
from .lever import LeverCollector

COLLECTORS: dict[str, type[Collector]] = {
    "ashby": AshbyCollector,
    "comeet": ComeetCollector,
    "lever": LeverCollector,
}


def get_collector_class(ats: str) -> type[Collector] | None:
    return COLLECTORS.get(ats)
