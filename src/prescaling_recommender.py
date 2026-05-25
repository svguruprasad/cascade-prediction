"""
Pre-Scaling Recommender
=======================
Given a cascade assessment, recommends specific quota increases
needed BEFORE a load event to prevent cascade failures.
"""

import sys
sys.path.insert(0, '.')

from dataclasses import dataclass
from typing import Dict, List, Tuple
from cascade_model import CascadeGraph, LoadEvent, Severity
from cascade_engine import assess_with_cascade, propagate_cascade


@dataclass
class QuotaRecommendation:
    quota_name: str
    current_limit: float
    current_utilization: float
    post_event_effective: float  # After load + cascade
    recommended_limit: float
    headroom_pct: float  # Target headroom percentage
    severity: Severity
    service: str
    increase_time_hours: float
    reason: str  # Why this needs increase


@dataclass
class PreScalingPlan:
    load_event: LoadEvent
    recommendations: List[QuotaRecommendation]
    total_quotas_at_risk: int
    fatal_risks: int
    lead_time_needed_hours: float  # Max of all increase times
    cascade_paths_mitigated: List[List[str]]


def recommend_prescaling(graph: CascadeGraph, load_event: LoadEvent,
                         headroom_target: float = 0.3,
                         alert_threshold: float = 0.8) -> PreScalingPlan:
    """
    Run cascade model and recommend quota increases for any quota
    that would exceed alert_threshold after the event.

    Args:
        graph: Service composition graph
        load_event: Planned capacity event
        headroom_target: Desired headroom after scaling (0.3 = 30%)
        alert_threshold: Ratio above which we recommend scaling (0.8 = 80%)
    """
    # Clone and apply load
    g = graph.clone()
    for name, load in load_event.loads.items():
        if name in g.quotas:
            g.quotas[name].utilization += load

    # Propagate cascades
    cascade_loads = propagate_cascade(g)

    recommendations = []
    for name, quota in g.quotas.items():
        effective = quota.utilization + cascade_loads[name]
        effective_ratio = effective / quota.limit if quota.limit > 0 else 0

        if effective_ratio >= alert_threshold:
            # Calculate recommended new limit
            recommended = effective * (1 + headroom_target)

            # Determine reason
            direct_load = load_event.loads.get(name, 0)
            cascade_load = cascade_loads[name]

            if direct_load > 0 and cascade_load > 0:
                reason = f"Direct load ({direct_load:.0f}) + cascade ({cascade_load:.0f})"
            elif cascade_load > 0:
                reason = f"Cascade-only: {cascade_load:.0f} units from upstream pressure"
            else:
                reason = f"Direct load: {direct_load:.0f} units"

            recommendations.append(QuotaRecommendation(
                quota_name=name,
                current_limit=graph.quotas[name].limit,  # Original limit
                current_utilization=graph.quotas[name].utilization,  # Original util
                post_event_effective=effective,
                recommended_limit=recommended,
                headroom_pct=headroom_target,
                severity=quota.severity,
                service=quota.service,
                increase_time_hours=quota.increase_time_hours,
                reason=reason
            ))

    # Sort by severity (fatal first) then by overshoot
    severity_order = {Severity.FATAL: 0, Severity.COMPLIANCE: 1,
                      Severity.DEGRADED: 2, Severity.COSMETIC: 3}
    recommendations.sort(key=lambda r: (severity_order[r.severity],
                                         -(r.post_event_effective / r.current_limit)))

    fatal_count = sum(1 for r in recommendations if r.severity == Severity.FATAL)
    max_lead_time = max((r.increase_time_hours for r in recommendations), default=0)

    # Identify which cascade paths are mitigated
    from cascade_engine import trace_cascade_paths
    paths = trace_cascade_paths(g, cascade_loads)

    return PreScalingPlan(
        load_event=load_event,
        recommendations=recommendations,
        total_quotas_at_risk=len(recommendations),
        fatal_risks=fatal_count,
        lead_time_needed_hours=max_lead_time,
        cascade_paths_mitigated=paths
    )


def print_prescaling_plan(plan: PreScalingPlan):
    """Pretty-print a pre-scaling plan."""
    print(f"\n{'═' * 75}")
    print(f"PRE-SCALING PLAN: {plan.load_event.name}")
    print(f"{'═' * 75}")
    print(f"  Quotas at risk: {plan.total_quotas_at_risk}")
    print(f"  Fatal risks: {plan.fatal_risks}")
    print(f"  Lead time needed: {plan.lead_time_needed_hours:.0f} hours "
          f"({plan.lead_time_needed_hours/24:.1f} days)")
    print(f"  Cascade paths: {len(plan.cascade_paths_mitigated)}")

    if not plan.recommendations:
        print("  ✅ No quota increases needed — all quotas have sufficient headroom.")
        return

    print(f"\n  {'Quota':<25} {'Service':<12} {'Sev':<10} {'Current':<12} "
          f"{'Post-Event':<12} {'Recommend':<12} {'Reason'}")
    print(f"  {'─' * 110}")

    for r in plan.recommendations:
        sev_icon = {"fatal": "🔴", "compliance": "🟠",
                    "degraded": "🟡", "cosmetic": "⚪"}[r.severity.value]
        print(f"  {r.quota_name:<25} {r.service:<12} {sev_icon} {r.severity.value:<7} "
              f"{r.current_limit:<12.0f} {r.post_event_effective:<12.0f} "
              f"{r.recommended_limit:<12.0f} {r.reason}")

    # Independent vs cascade comparison
    direct_only = [r for r in plan.recommendations
                   if plan.load_event.loads.get(r.quota_name, 0) > 0]
    cascade_only = [r for r in plan.recommendations
                    if plan.load_event.loads.get(r.quota_name, 0) == 0]

    print(f"\n  DETECTION COMPARISON:")
    print(f"  • Independent monitoring would flag: {len(direct_only)} quotas")
    print(f"  • Cascade model additionally flags: {len(cascade_only)} quotas")
    print(f"  • Invisible without cascade analysis: "
          f"{', '.join(r.quota_name for r in cascade_only[:5])}")


if __name__ == "__main__":
    from topologies import ALL_TOPOLOGIES
    from cascade_model import LoadEvent

    events = [
        LoadEvent("Migration cutover: 2500 calls", {"concurrent_calls": 2500}, "migration"),
        LoadEvent("Open enrollment: +80% calls", {"concurrent_calls": 2800}, "campaign"),
        LoadEvent("Omnichannel launch", {"concurrent_calls": 1500, "concurrent_chats": 2000}, "migration"),
    ]

    for topo_name in ["basic_voice", "full_analytics", "omnichannel"]:
        print(f"\n\n{'█' * 75}")
        print(f"  TOPOLOGY: {topo_name}")
        print(f"{'█' * 75}")

        graph = ALL_TOPOLOGIES[topo_name]()
        for event in events:
            # Skip if event references quotas not in topology
            if not any(q in graph.quotas for q in event.loads):
                continue
            plan = recommend_prescaling(graph, event)
            print_prescaling_plan(plan)
