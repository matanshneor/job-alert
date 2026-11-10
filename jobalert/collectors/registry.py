"""Map ATS names from companies.yaml to collector classes."""
from __future__ import annotations

from .ashby import AshbyCollector
from .base import Collector
from .comeet import ComeetCollector
from .eightfold import EightfoldCollector
from .greenhouse import GreenhouseCollector
from .lever import LeverCollector
from .mizrahi import MizrahiCollector
from .oracle_hcm import OracleHcmCollector
from .workday import WorkdayCollector

COLLECTORS: dict[str, type[Collector]] = {
    "ashby": AshbyCollector,
    "comeet": ComeetCollector,
    "lever": LeverCollector,
    "greenhouse": GreenhouseCollector,
    "eightfold": EightfoldCollector,
    "workday": WorkdayCollector,
    "oracle_hcm": OracleHcmCollector,
    "mizrahi": MizrahiCollector,
}


def get_collector_class(ats: str) -> type[Collector] | None:
    return COLLECTORS.get(ats)
