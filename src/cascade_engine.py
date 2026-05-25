"""
Cascade Propagation Engine
==========================
Propagates load through dependency graph and detects exhaustion cascades.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple
from cascade_model import CascadeGraph, DependencyEdge, LoadEvent, Severity
import numpy as np


@dataclass
class CascadeResult:
    exhausted_quotas: Dict[str, float]
    cascade_loads: Dict[str, float]
    cascade_paths: List[List[str]]
    fatal_failures: List[str]
    degraded_services: List[str]
    compliance_violations: List[str]
    total_cascade_amplification: float


def compute_cascade_load(edge: DependencyEdge, source_ratio: float,
                         source_load_increase: float) -> float:
    """
    Compute additional absolute load on target from source pressure.

    The cascade load is proportional to the INCOMING load on the source,
    multiplied by the amplification factor when the source is under pressure.

    source_load_increase: how much additional load the source received
                          (from the planned event or upstream cascades)
    """
    if source_ratio < edge.threshold:
        return 0.0

    # How stressed is the source? (0 at threshold, 1 at limit)
    normalized_pressure = min((source_ratio - edge.threshold) / (1.0 - edge.threshold), 1.0)

    if source_ratio < 1.0:
        # Congestion: each unit of source load produces amplification * pressure^2 downstream
        return edge.amplification * (normalized_pressure ** 2) * source_load_increase
    else:
        # Exhausted: retry storms multiply the load
        return edge.amplification * edge.retry_multiplier * source_load_increase


def propagate_cascade(graph: CascadeGraph, max_iterations: int = 15,
                      convergence_threshold: float = 0.1) -> Dict[str, float]:
    """
    Iteratively propagate cascade effects until convergence.
    Returns {quota_name: total_cascade_load_added}.

    Caps cascade load per quota to 10x its limit to prevent runaway.
    """
    cascade_loads = {name: 0.0 for name in graph.quotas}

    for iteration in range(max_iterations):
        new_loads = {name: 0.0 for name in graph.quotas}

        for edge in graph.active_edges():
            if edge.source not in graph.quotas:
                continue
            source = graph.quotas[edge.source]
            effective_util = source.utilization + cascade_loads[edge.source]
            effective_ratio = effective_util / source.limit if source.limit > 0 else 0

            # Source load increase = how much was added to this node
            # (from direct event load + cascade from upstream)
            source_load_increase = max(0, effective_util - source.utilization) + cascade_loads[edge.source]
            if source_load_increase <= 0:
                source_load_increase = max(0, effective_util - edge.threshold * source.limit)

            additional = compute_cascade_load(edge, effective_ratio, source_load_increase)

            # Cap to prevent runaway
            target = graph.quotas.get(edge.target)
            if target:
                additional = min(additional, target.limit * 2)

            new_loads[edge.target] = new_loads.get(edge.target, 0) + additional

        # Cap total cascade load per quota to 10x limit
        for name in graph.quotas:
            new_loads[name] = min(new_loads[name], graph.quotas[name].limit * 10)

        # Check convergence
        max_delta = max(abs(new_loads[n] - cascade_loads[n]) for n in graph.quotas)
        cascade_loads = new_loads

        if max_delta < convergence_threshold:
            break

    return cascade_loads


def trace_cascade_paths(graph: CascadeGraph, cascade_loads: Dict[str, float]) -> List[List[str]]:
    """Trace which paths contributed to cascade failures."""
    paths = []
    for edge in graph.active_edges():
        source = graph.quotas.get(edge.source)
        target = graph.quotas.get(edge.target)
        if source and target:
            effective_source = source.utilization + cascade_loads.get(edge.source, 0)
            effective_target = target.utilization + cascade_loads.get(edge.target, 0)
            if effective_source >= source.limit * edge.threshold and effective_target >= target.limit:
                paths.append([edge.source, edge.target])
    return paths


def assess_with_cascade(graph: CascadeGraph, load_event: LoadEvent) -> CascadeResult:
    """
    Full cascade-aware assessment of a load event.
    """
    # Clone graph and apply load
    g = graph.clone()
    for name, load in load_event.loads.items():
        if name in g.quotas:
            g.quotas[name].utilization += load

    # Propagate cascades
    cascade_loads = propagate_cascade(g)

    # Classify results
    exhausted = {}
    fatal = []
    degraded = []
    compliance = []

    for name, quota in g.quotas.items():
        effective = quota.utilization + cascade_loads[name]
        if effective >= quota.limit:
            exhausted[name] = effective
            if quota.severity == Severity.FATAL:
                fatal.append(name)
            elif quota.severity == Severity.DEGRADED:
                degraded.append(name)
            elif quota.severity == Severity.COMPLIANCE:
                compliance.append(name)

    total_amplification = sum(cascade_loads.values())
    paths = trace_cascade_paths(g, cascade_loads)

    return CascadeResult(
        exhausted_quotas=exhausted,
        cascade_loads=cascade_loads,
        cascade_paths=paths,
        fatal_failures=fatal,
        degraded_services=degraded,
        compliance_violations=compliance,
        total_cascade_amplification=total_amplification
    )


def assess_independent(graph: CascadeGraph, load_event: LoadEvent) -> Dict[str, bool]:
    """Standard independent assessment — no cascade propagation."""
    results = {}
    for name, quota in graph.quotas.items():
        new_util = quota.utilization + load_event.loads.get(name, 0.0)
        results[name] = new_util >= quota.limit
    return results
