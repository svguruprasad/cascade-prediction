"""
Cascade Quota Exhaustion Prediction — Core Model
=================================================
Configurable service composition graphs with severity levels,
variable topologies, and cascade propagation.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


class Severity(Enum):
    """Impact severity when a quota exhausts."""
    FATAL = "fatal"          # Service outage (calls dropped)
    DEGRADED = "degraded"    # Feature loss (no analytics, no recording)
    COMPLIANCE = "compliance"  # Regulatory violation (CTR loss)
    COSMETIC = "cosmetic"    # Minor impact (delayed metrics)


@dataclass
class Quota:
    name: str
    limit: float
    utilization: float = 0.0
    severity: Severity = Severity.FATAL
    service: str = ""  # Which AWS service this belongs to
    increase_time_hours: float = 24.0  # How long a quota increase takes

    @property
    def headroom(self) -> float:
        return max(0, self.limit - self.utilization)

    @property
    def ratio(self) -> float:
        return self.utilization / self.limit if self.limit > 0 else 0.0

    @property
    def is_exhausted(self) -> bool:
        return self.utilization >= self.limit


@dataclass
class DependencyEdge:
    source: str
    target: str
    amplification: float
    threshold: float = 0.7
    retry_multiplier: float = 3.0
    enabled: bool = True  # Can be toggled per topology config


@dataclass
class LoadEvent:
    """A step-function load change (migration, campaign, etc.)."""
    name: str
    loads: Dict[str, float]  # {quota_name: additional_load}
    event_type: str = "migration"  # migration, campaign, seasonal, failure
    duration_hours: float = float('inf')  # How long the load sustains
    ramp_minutes: float = 0.0  # 0 = instant step, >0 = gradual ramp


class CascadeGraph:
    """Configurable directed graph of quota dependencies."""

    def __init__(self):
        self.quotas: Dict[str, Quota] = {}
        self.edges: List[DependencyEdge] = []

    def add_quota(self, name: str, limit: float, utilization: float = 0.0,
                  severity: Severity = Severity.FATAL, service: str = "",
                  increase_time_hours: float = 24.0):
        self.quotas[name] = Quota(
            name=name, limit=limit, utilization=utilization,
            severity=severity, service=service,
            increase_time_hours=increase_time_hours
        )

    def add_edge(self, source: str, target: str, amplification: float,
                 threshold: float = 0.7, retry_multiplier: float = 3.0,
                 enabled: bool = True):
        self.edges.append(DependencyEdge(
            source=source, target=target,
            amplification=amplification,
            threshold=threshold,
            retry_multiplier=retry_multiplier,
            enabled=enabled
        ))

    def active_edges(self) -> List[DependencyEdge]:
        return [e for e in self.edges if e.enabled]

    def clone(self) -> 'CascadeGraph':
        """Deep copy for running scenarios without mutation."""
        g = CascadeGraph()
        for name, q in self.quotas.items():
            g.add_quota(name, q.limit, q.utilization, q.severity,
                        q.service, q.increase_time_hours)
        for e in self.edges:
            g.add_edge(e.source, e.target, e.amplification,
                       e.threshold, e.retry_multiplier, e.enabled)
        return g
