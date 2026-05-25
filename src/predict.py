"""
predict.py — Cascade Quota Exhaustion Predictor
================================================
End-to-end: collect current utilization → run cascade model → output recommendations.

Usage:
    # Predict cascade risk for a planned 2500-call migration
    python predict.py --event "concurrent_calls:2500" --topology full_analytics --region us-east-1

    # Use pre-collected utilization file
    python predict.py --event "concurrent_calls:2500" --utilization utilization.json

    # Multiple load changes
    python predict.py --event "concurrent_calls:2500,lambda_concurrency:500"
"""

import argparse
import json
import sys
from typing import Dict

from cascade_model import CascadeGraph, LoadEvent, Severity
from cascade_engine import assess_with_cascade, propagate_cascade
from topologies import ALL_TOPOLOGIES
from prescaling_recommender import recommend_prescaling, print_prescaling_plan


def parse_event(event_str: str) -> Dict[str, float]:
    """Parse event string like 'concurrent_calls:2500,lambda_concurrency:500'"""
    loads = {}
    for pair in event_str.split(","):
        parts = pair.strip().split(":")
        if len(parts) == 2:
            loads[parts[0].strip()] = float(parts[1].strip())
    return loads


def apply_utilization_from_file(graph: CascadeGraph, util_file: str):
    """Override graph utilization AND limits with real CloudWatch/Service Quotas values."""
    with open(util_file, 'r') as f:
        data = json.load(f)

    # Apply real utilization
    for quota_name, info in data.get("quotas", {}).items():
        if quota_name in graph.quotas and info["value"] >= 0:
            graph.quotas[quota_name].utilization = info["value"]
            print(f"  Set {quota_name} utilization = {info['value']:.1f}")

    # Apply real limits (overrides topology defaults)
    limit_map = {
        "lambda_concurrency": "lambda_concurrency",
        "connect_concurrent_calls": "concurrent_calls",
        "dynamodb_table_wcu": "dynamodb_wcu",
        "dynamodb_table_rcu": "dynamodb_rcu",
        "kinesis_shards": "kinesis_records_sec",
    }
    for limit_name, info in data.get("limits", {}).items():
        graph_name = limit_map.get(limit_name)
        if graph_name and graph_name in graph.quotas and info.get("value", -1) > 0:
            old_limit = graph.quotas[graph_name].limit
            graph.quotas[graph_name].limit = info["value"]
            print(f"  Set {graph_name} limit = {info['value']:.0f} (was {old_limit:.0f})")


def main():
    parser = argparse.ArgumentParser(
        description="Predict cascading quota exhaustion for a planned capacity event"
    )
    parser.add_argument("--event", required=True,
                        help="Load event, e.g. 'concurrent_calls:2500'")
    parser.add_argument("--topology", default="full_analytics",
                        choices=list(ALL_TOPOLOGIES.keys()),
                        help="Service composition topology")
    parser.add_argument("--utilization", default=None,
                        help="JSON file with current utilization (from collect_utilization.py)")
    parser.add_argument("--headroom", type=float, default=0.3,
                        help="Target headroom after scaling (default: 0.3 = 30%%)")
    parser.add_argument("--threshold", type=float, default=0.8,
                        help="Alert threshold ratio (default: 0.8 = 80%%)")
    parser.add_argument("--output", default=None,
                        help="Output JSON file for recommendations")
    args = parser.parse_args()

    # Build graph
    graph = ALL_TOPOLOGIES[args.topology]()
    print(f"Topology: {args.topology} ({len(graph.quotas)} quotas, {len(graph.edges)} edges)")

    # Apply real utilization if provided
    if args.utilization:
        print(f"\nLoading utilization from {args.utilization}:")
        apply_utilization_from_file(graph, args.utilization)

    # Parse load event
    loads = parse_event(args.event)
    event = LoadEvent(name=f"Planned: {args.event}", loads=loads, event_type="planned")
    print(f"\nPlanned event: {loads}")

    # Run prediction
    plan = recommend_prescaling(graph, event,
                                headroom_target=args.headroom,
                                alert_threshold=args.threshold)

    # Output
    print_prescaling_plan(plan)

    if args.output:
        output_data = {
            "topology": args.topology,
            "event": loads,
            "quotas_at_risk": plan.total_quotas_at_risk,
            "fatal_risks": plan.fatal_risks,
            "lead_time_hours": plan.lead_time_needed_hours,
            "recommendations": [
                {
                    "quota": r.quota_name,
                    "service": r.service,
                    "severity": r.severity.value,
                    "current_limit": r.current_limit,
                    "recommended_limit": r.recommended_limit,
                    "reason": r.reason
                }
                for r in plan.recommendations
            ]
        }
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"\n  Recommendations saved to {args.output}")


if __name__ == "__main__":
    main()
