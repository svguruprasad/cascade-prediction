"""Test predictions at different Lambda concurrency limits."""
import sys
sys.path.insert(0, '.')
from cascade_model import LoadEvent
from topologies import topology_full_analytics
from prescaling_recommender import recommend_prescaling

event = LoadEvent('Migration +2500 calls', {'concurrent_calls': 2500.0}, 'migration')

for limit in [1000, 3000, 5000, 10000]:
    g = topology_full_analytics()
    g.quotas['lambda_concurrency'].limit = limit
    plan = recommend_prescaling(g, event)

    lambda_rec = next((r for r in plan.recommendations if r.quota_name == 'lambda_concurrency'), None)
    fatal_names = [r.quota_name for r in plan.recommendations if r.severity.value == 'fatal']

    print(f"Lambda limit: {limit:>6}")
    print(f"  Quotas at risk: {plan.total_quotas_at_risk}")
    print(f"  Fatal risks: {plan.fatal_risks} ({', '.join(fatal_names)})")
    if lambda_rec:
        print(f"  Lambda: post_event={lambda_rec.post_event_effective:.0f}, recommend={lambda_rec.recommended_limit:.0f}")
    else:
        print(f"  Lambda: NOT at risk (sufficient headroom)")
    print()
