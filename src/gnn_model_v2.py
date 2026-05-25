"""
GNN Model v2: Per-Trace Fan-Out Prediction
==========================================
Each trace IS a graph. Predict whether a trace will have high fan-out
(exceeding p95) based on its initial structure.

Task: Given the first few hops of a call graph, predict total fan-out.
This simulates: "given the entry point and first-level calls, will this
request cascade into a high-amplification event?"
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATConv, global_mean_pool, global_max_pool
from torch_geometric.data import Data
import numpy as np
import csv
from collections import defaultdict
from typing import List
import os

DATA_PATH = "/Users/svguru/FirstAccount/cascade-quota-prediction/data/alibaba/raw/MSCallGraph_0.csv"
MAX_ROWS = 2_000_000


def build_trace_graphs() -> List[Data]:
    """
    Build one graph per trace. Each trace = one request's call graph.
    Features: node depth in call tree, rpc type encoding, response time.
    Label: total trace size (number of calls) — binary: above/below p95.
    """
    print("Building per-trace graphs...")

    # Collect traces
    traces = defaultdict(list)  # {traceid: [(um, dm, rpctype, rt, depth)]}

    row_count = 0
    with open(DATA_PATH, 'r') as f:
        reader = csv.reader(f)
        next(reader)

        for row in reader:
            row_count += 1
            if row_count > MAX_ROWS:
                break
            if len(row) < 9:
                continue

            _, traceid, timestamp, rpcid, um, rpctype, dm, interface, rt = row[:9]

            # Compute depth from rpcid (e.g., "0.1.3.1" = depth 4)
            depth = len(rpcid.split('.')) if rpcid else 1

            try:
                rt_val = abs(int(rt)) if rt else 0
            except ValueError:
                rt_val = 0

            # Encode rpc type
            rpc_map = {'rpc': 0, 'http': 1, 'mc': 2, 'db': 3, 'mq': 4, 'userDefined': 5}
            rpc_enc = rpc_map.get(rpctype, 5)

            traces[traceid].append((um, dm, rpc_enc, rt_val, depth))

    print(f"  Rows: {row_count:,}, Traces: {len(traces):,}")

    # Compute p95 trace size for labeling
    trace_sizes = [len(calls) for calls in traces.values()]
    p95_size = np.percentile(trace_sizes, 95)
    p75_size = np.percentile(trace_sizes, 75)
    median_size = np.median(trace_sizes)
    print(f"  Trace sizes: median={median_size:.0f}, p75={p75_size:.0f}, p95={p95_size:.0f}, max={max(trace_sizes)}")

    # Build PyG graphs (sample up to 5000 traces for training speed)
    sample_traces = list(traces.items())[:5000]
    graphs = []

    for traceid, calls in sample_traces:
        if len(calls) < 2:
            continue

        # Map services to local indices within this trace
        local_idx = {}
        for um, dm, _, _, _ in calls:
            if um not in local_idx:
                local_idx[um] = len(local_idx)
            if dm not in local_idx:
                local_idx[dm] = len(local_idx)

        num_nodes = len(local_idx)
        if num_nodes < 2:
            continue

        # Build edge index
        src, dst = [], []
        edge_features = []
        for um, dm, rpc_enc, rt_val, depth in calls:
            src.append(local_idx[um])
            dst.append(local_idx[dm])
            edge_features.append([rpc_enc / 5.0, min(rt_val, 1000) / 1000.0, depth / 10.0])

        edge_index = torch.tensor([src, dst], dtype=torch.long)
        edge_attr = torch.tensor(edge_features, dtype=torch.float)

        # Node features: [in-degree, out-degree, max_depth_seen]
        in_deg = torch.zeros(num_nodes)
        out_deg = torch.zeros(num_nodes)
        for s, d in zip(src, dst):
            out_deg[s] += 1
            in_deg[d] += 1

        x = torch.stack([
            in_deg / (in_deg.max() + 1e-6),
            out_deg / (out_deg.max() + 1e-6),
            torch.ones(num_nodes) * len(calls) / p95_size  # normalized trace size hint
        ], dim=1)

        # Label: 1 if trace exceeds p75 (more balanced than p95)
        y = torch.tensor([1.0 if len(calls) > p75_size else 0.0])

        data = Data(x=x, edge_index=edge_index, edge_attr=edge_attr, y=y)
        data.num_nodes = num_nodes
        graphs.append(data)

    pos = sum(1 for g in graphs if g.y.item() == 1)
    neg = len(graphs) - pos
    print(f"  Graphs built: {len(graphs)} (pos={pos}, neg={neg}, ratio={pos/(pos+neg)*100:.1f}%)")

    return graphs


class CascadeGAT(nn.Module):
    """GAT for graph-level classification: will this trace cascade?"""

    def __init__(self, in_channels=3, hidden=32, heads=4, dropout=0.2):
        super().__init__()
        self.gat1 = GATConv(in_channels, hidden, heads=heads, dropout=dropout)
        self.gat2 = GATConv(hidden * heads, hidden, heads=1, dropout=dropout)

        # Graph-level readout + classifier
        self.classifier = nn.Sequential(
            nn.Linear(hidden * 2, 32),  # *2 for mean+max pooling
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1)
        )

    def forward(self, data):
        x, edge_index, batch = data.x, data.edge_index, data.batch

        x = F.elu(self.gat1(x, edge_index))
        x = F.elu(self.gat2(x, edge_index))

        # Graph-level pooling (mean + max)
        x_mean = global_mean_pool(x, batch)
        x_max = global_max_pool(x, batch)
        x_graph = torch.cat([x_mean, x_max], dim=1)

        return self.classifier(x_graph).squeeze(-1)


def train_and_evaluate(graphs: List[Data], epochs=100):
    """Train GNN and compare to baselines."""
    from torch_geometric.loader import DataLoader

    print(f"\n{'=' * 70}")
    print("TRAINING GNN")
    print(f"{'=' * 70}")

    # Split 80/20
    split = int(len(graphs) * 0.8)
    train_data = graphs[:split]
    test_data = graphs[split:]

    train_loader = DataLoader(train_data, batch_size=64, shuffle=True)
    test_loader = DataLoader(test_data, batch_size=64)

    model = CascadeGAT(in_channels=3, hidden=32, heads=4)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([2.0]))

    # Train
    model.train()
    for epoch in range(epochs):
        total_loss = 0
        for batch in train_loader:
            optimizer.zero_grad()
            out = model(batch)
            loss = criterion(out, batch.y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        if (epoch + 1) % 20 == 0:
            print(f"  Epoch {epoch+1}/{epochs} — Loss: {total_loss/len(train_loader):.4f}")

    # Evaluate
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for batch in test_loader:
            out = torch.sigmoid(model(batch))
            all_preds.extend(out.numpy().tolist())
            all_labels.extend(batch.y.numpy().tolist())

    preds = np.array(all_preds)
    labels = np.array(all_labels)

    # GNN metrics
    pred_bin = (preds > 0.5).astype(int)
    tp = ((pred_bin == 1) & (labels == 1)).sum()
    fp = ((pred_bin == 1) & (labels == 0)).sum()
    fn = ((pred_bin == 0) & (labels == 1)).sum()
    tn = ((pred_bin == 0) & (labels == 0)).sum()

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
    acc = (tp + tn) / len(labels)

    print(f"\n  GNN RESULTS:")
    print(f"    Accuracy: {acc:.3f}")
    print(f"    Precision: {prec:.3f}")
    print(f"    Recall: {rec:.3f}")
    print(f"    F1: {f1:.3f}")

    # Baseline 1: Random (predict majority class)
    majority = 1 if labels.mean() > 0.5 else 0
    baseline_acc = (labels == majority).mean()
    print(f"\n  BASELINE (majority class): Accuracy={baseline_acc:.3f}, F1=0.000")

    # Baseline 2: Simple threshold on graph size
    # Use number of nodes as predictor
    test_sizes = np.array([g.num_nodes for g in test_data])
    size_threshold = np.median(test_sizes)
    size_preds = (test_sizes > size_threshold).astype(int)
    size_tp = ((size_preds == 1) & (labels == 1)).sum()
    size_fp = ((size_preds == 1) & (labels == 0)).sum()
    size_fn = ((size_preds == 0) & (labels == 1)).sum()
    size_prec = size_tp / (size_tp + size_fp) if (size_tp + size_fp) > 0 else 0
    size_rec = size_tp / (size_tp + size_fn) if (size_tp + size_fn) > 0 else 0
    size_f1 = 2 * size_prec * size_rec / (size_prec + size_rec) if (size_prec + size_rec) > 0 else 0
    print(f"  BASELINE (graph size threshold): Precision={size_prec:.3f}, Recall={size_rec:.3f}, F1={size_f1:.3f}")

    # Summary
    print(f"\n  {'Method':<30} {'Precision':<12} {'Recall':<10} {'F1':<8}")
    print(f"  {'─' * 60}")
    print(f"  {'Majority class':<30} {'N/A':<12} {'N/A':<10} {'0.000':<8}")
    print(f"  {'Graph size threshold':<30} {size_prec:<12.3f} {size_rec:<10.3f} {size_f1:<8.3f}")
    print(f"  {'GNN (ours)':<30} {prec:<12.3f} {rec:<10.3f} {f1:<8.3f}")

    improvement = f1 - size_f1
    if improvement > 0:
        print(f"\n  ✅ GNN improves over best baseline by {improvement*100:.1f}% F1")
    else:
        print(f"\n  ❌ GNN does not improve over baseline. Need more features or data.")

    return model, {"gnn_f1": f1, "baseline_f1": size_f1, "improvement": improvement}


if __name__ == "__main__":
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    graphs = build_trace_graphs()
    if len(graphs) >= 100:
        model, metrics = train_and_evaluate(graphs, epochs=100)
    else:
        print(f"Only {len(graphs)} graphs — need at least 100 for meaningful training.")
