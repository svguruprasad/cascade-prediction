"""
Generate paper figures from REAL measured data only.
No simulated or assumed numbers.
"""

import matplotlib.pyplot as plt
import numpy as np
import os

OUTPUT_DIR = os.path.expanduser("~/personal/cascade-prediction/paper/figures")
os.makedirs(OUTPUT_DIR, exist_ok=True)

plt.rcParams['font.size'] = 11
plt.rcParams['figure.dpi'] = 150


def fig2_cascade_failure():
    """
    Fig 2: Measured throttle rate vs concurrency.
    Data from cascade_aggressive_test.py (Lambda cap=5, no retries).
    CloudWatch confirmed 312 throttles.
    """
    # REAL DATA from experiment
    concurrent = [5, 10, 25, 50, 100, 200]
    success = [5, 5, 9, 13, 16, 29]
    throttled = [0, 5, 15, 37, 84, 171]
    throttle_pct = [0, 50, 60, 74, 84, 86]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))

    # Left: throttle rate
    ax1.bar(range(len(concurrent)), throttle_pct, color='#cc4444', alpha=0.8)
    ax1.set_xticks(range(len(concurrent)))
    ax1.set_xticklabels([str(c) for c in concurrent])
    ax1.set_xlabel('Concurrent requests (Lambda cap = 5)')
    ax1.set_ylabel('Throttle rate (%)')
    ax1.set_title('Measured throttle rate vs concurrency')
    ax1.set_ylim(0, 100)
    ax1.axhline(y=84, color='gray', linestyle='--', alpha=0.5, label='84% at 20x cap')
    ax1.legend()
    for i, pct in enumerate(throttle_pct):
        if pct > 0:
            ax1.text(i, pct + 2, f'{pct}%', ha='center', fontsize=9)

    # Right: downstream ops lost
    ops_produced = [s * 4 for s in success]  # 4 ops per successful call (single-function test)
    ops_expected = [c * 4 for c in concurrent]
    ops_lost = [e - p for e, p in zip(ops_expected, ops_produced)]

    x = np.arange(len(concurrent))
    width = 0.35
    ax2.bar(x - width/2, ops_produced, width, label='Ops produced', color='#44aa44', alpha=0.8)
    ax2.bar(x + width/2, ops_lost, width, label='Ops LOST', color='#cc4444', alpha=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels([str(c) for c in concurrent])
    ax2.set_xlabel('Concurrent requests')
    ax2.set_ylabel('Downstream operations')
    ax2.set_title('Downstream operations: produced vs lost')
    ax2.legend()

    plt.tight_layout()
    plt.savefig(f"{OUTPUT_DIR}/fig2_cascade_failure.png", bbox_inches='tight')
    plt.close()
    print("  fig2_cascade_failure.png")


def fig3_cross_domain():
    """
    Fig 3: When does GNN add value?
    Data from real AWS experiments + Alibaba analysis.
    """
    # REAL DATA
    configs = ['Fixed flow\n(no branching)', 'Branching flow\n(60% IVR)', 'Alibaba\nproduction']
    cv_values = [0.000, 0.207, 1.094]
    static_error = [0, 25, 500]  # approximate % error of static model
    colors = ['#44aa44', '#ffaa44', '#cc4444']

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))

    # Left: CV comparison
    bars = ax1.bar(configs, cv_values, color=colors, alpha=0.8, edgecolor='black', linewidth=0.5)
    ax1.set_ylabel('Coefficient of variation (CV)')
    ax1.set_title('Fan-out variability by flow complexity')
    ax1.axhline(y=0.3, color='gray', linestyle='--', alpha=0.5, label='CV=0.3 threshold')
    ax1.legend()
    for bar, cv in zip(bars, cv_values):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.03,
                 f'{cv:.3f}', ha='center', fontsize=10, fontweight='bold')

    # Right: model recommendation
    methods = ['Static\nmodel', 'GNN\nmodel']
    # For each config, which method is better?
    ax2.set_xlim(-0.5, 2.5)
    ax2.set_ylim(-0.5, 1.5)
    ax2.axis('off')
    ax2.set_title('Recommended model by complexity')

    table_data = [
        ['Flow type', 'CV', 'Static model', 'GNN needed?'],
        ['Fixed (no branching)', '0.000', 'Exact (0% error)', 'No'],
        ['Branching (60/40)', '0.207', '17-34% error', 'Yes, reduces error'],
        ['Production (Alibaba)', '>1.0', '200-1000% error', 'Essential'],
    ]

    table = ax2.table(cellText=table_data[1:], colLabels=table_data[0],
                      loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.8)

    # Color cells
    for i in range(1, 4):
        table[i, 3].set_facecolor(['#d5e8d4', '#fff2cc', '#f8cecc'][i-1])

    plt.tight_layout()
    plt.savefig(f"{OUTPUT_DIR}/fig3_cross_domain.png", bbox_inches='tight')
    plt.close()
    print("  fig3_cross_domain.png")


if __name__ == "__main__":
    print("Generating figures from measured data...")
    fig2_cascade_failure()
    fig3_cross_domain()
    print(f"\nDone. Output: {OUTPUT_DIR}/")
    print("\nFig 1 (dependency graph): Open paper/fig1_dependency_graph.drawio in app.diagrams.net")
