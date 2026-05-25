"""
Alibaba Trace: Check if per-trace fan-out varies over time.
The key question: does the SAME upstream service call different numbers
of downstream services at different times?
"""

import csv
from collections import defaultdict
import statistics

DATA_PATH = "/Users/svguru/FirstAccount/cascade-quota-prediction/data/alibaba/raw/MSCallGraph_0.csv"
MAX_ROWS = 2_000_000
TIME_WINDOW = 300_000  # 5 minutes in ms


def main():
    print("Analyzing temporal variability of fan-out...")

    # Per (um→dm) pair, track call count per time window
    # Use full hash (not truncated)
    pair_per_window = defaultdict(lambda: defaultdict(int))

    # Per trace: count total calls
    trace_call_count = defaultdict(int)

    # Per UM: count how many DMs it calls per trace
    um_fanout_per_trace = defaultdict(list)  # {um: [fanout_in_trace1, fanout_in_trace2, ...]}

    # Track per-trace structure
    trace_ums = defaultdict(lambda: defaultdict(int))  # {traceid: {um: call_count_to_dms}}

    row_count = 0
    with open(DATA_PATH, 'r') as f:
        reader = csv.reader(f)
        next(reader)  # skip header

        for row in reader:
            row_count += 1
            if row_count > MAX_ROWS:
                break
            if len(row) < 9:
                continue

            _, traceid, timestamp, rpcid, um, rpctype, dm, interface, rt = row[:9]

            try:
                ts = int(timestamp)
            except ValueError:
                continue

            window = ts // TIME_WINDOW
            pair_per_window[(um, dm)][window] += 1
            trace_ums[traceid][um] += 1

    # Compute per-UM fan-out variability across traces
    print(f"\n  Processed {row_count:,} rows")
    print(f"  Unique traces: {len(trace_ums):,}")

    # For each UM, collect its fan-out (number of calls it makes) per trace
    um_trace_fanouts = defaultdict(list)
    for traceid, ums in trace_ums.items():
        for um, count in ums.items():
            um_trace_fanouts[um].append(count)

    # Find UMs with enough traces to measure variability
    print(f"\n{'=' * 70}")
    print("PER-SERVICE FAN-OUT VARIABILITY (across traces)")
    print(f"{'=' * 70}")
    print(f"\n  {'Service (UM)':<20} {'Traces':<10} {'Mean':<8} {'StdDev':<8} {'CV':<8} {'Min':<6} {'Max':<6} {'Variable?'}")
    print(f"  {'─' * 80}")

    variable_count = 0
    constant_count = 0
    all_cvs = []

    # Sort by number of traces (most active services first)
    sorted_ums = sorted(um_trace_fanouts.items(), key=lambda x: -len(x[1]))

    for um, fanouts in sorted_ums[:30]:
        if len(fanouts) < 10:
            continue

        mean = statistics.mean(fanouts)
        stdev = statistics.stdev(fanouts)
        cv = stdev / mean if mean > 0 else 0
        all_cvs.append(cv)

        is_variable = cv > 0.3
        if is_variable:
            variable_count += 1
        else:
            constant_count += 1

        marker = "⚡" if is_variable else "  "
        print(f"  {marker}{um[:18]:<20} {len(fanouts):<10} {mean:<8.1f} {stdev:<8.1f} {cv:<8.2f} {min(fanouts):<6} {max(fanouts):<6} {'YES' if is_variable else 'no'}")

    print(f"\n  SUMMARY (top 30 most active services):")
    print(f"    Variable (CV > 0.3): {variable_count}")
    print(f"    Constant (CV ≤ 0.3): {constant_count}")

    if all_cvs:
        print(f"    Mean CV across services: {statistics.mean(all_cvs):.3f}")
        print(f"    Median CV: {statistics.median(all_cvs):.3f}")

    # Now check TEMPORAL variability for top pairs
    print(f"\n{'=' * 70}")
    print("TEMPORAL VARIABILITY (same pair, different time windows)")
    print(f"{'=' * 70}")

    # Find pairs with enough windows
    pair_totals = {pair: sum(wins.values()) for pair, wins in pair_per_window.items()}
    top_pairs = sorted(pair_totals.items(), key=lambda x: -x[1])[:30]

    print(f"\n  {'Pair':<40} {'Windows':<8} {'Mean':<8} {'StdDev':<8} {'CV':<8} {'Variable?'}")
    print(f"  {'─' * 80}")

    temporal_variable = 0
    temporal_constant = 0

    for (um, dm), total in top_pairs:
        windows = pair_per_window[(um, dm)]
        counts = list(windows.values())
        if len(counts) < 3:
            continue

        mean = statistics.mean(counts)
        stdev = statistics.stdev(counts)
        cv = stdev / mean if mean > 0 else 0

        is_variable = cv > 0.3
        if is_variable:
            temporal_variable += 1
        else:
            temporal_constant += 1

        marker = "⚡" if is_variable else "  "
        print(f"  {marker}{um[:18]}→{dm[:18]:<18} {len(counts):<8} {mean:<8.1f} {stdev:<8.1f} {cv:<8.2f} {'YES' if is_variable else 'no'}")

    print(f"\n  TEMPORAL SUMMARY:")
    print(f"    Variable over time (CV > 0.3): {temporal_variable}")
    print(f"    Constant over time (CV ≤ 0.3): {temporal_constant}")

    # Final verdict
    print(f"\n{'=' * 70}")
    print("VERDICT: IS THERE SOMETHING FOR ML TO LEARN?")
    print(f"{'=' * 70}")

    if variable_count > constant_count or temporal_variable > temporal_constant:
        print(f"\n  ✅ YES — Fan-out varies significantly across traces AND over time.")
        print(f"     A static model with fixed amplification factors would be inaccurate.")
        print(f"     ML can learn the dynamic patterns.")
    else:
        print(f"\n  ❌ NO — Fan-out is mostly constant.")
        print(f"     Static model is sufficient. ML would overfit to noise.")


if __name__ == "__main__":
    main()
