# Predicting Cascading Quota Exhaustion in Composed Cloud Services

A framework that predicts cascading service quota failures in cloud service compositions using Graph Attention Networks (GAT) trained on production microservice traces.

## The Problem

Cloud platforms enforce quotas per-service independently. But services are composed — a contact center uses compute, database, streaming, and AI services together. When one service throttles, downstream services receive zero operations. Independent monitoring misses 92% of these cascade failures.

## Results

| Method | Precision | Recall | F1 |
|--------|-----------|--------|-----|
| Majority class baseline | — | — | 0.000 |
| Graph size threshold | 0.527 | 0.995 | 0.689 |
| **GNN (GAT, ours)** | **1.000** | **0.944** | **0.971** |

Trained on 4,976 production call graphs from Alibaba microservice traces (43,501 traces, 2M calls).

## Quick Start

```bash
pip install -r requirements.txt
cd src

# 1. Collect current utilization from CloudWatch
python collect_utilization.py --region us-east-1 --table MyTable --stream MyStream

# 2. Predict cascade risk for a planned event
python predict.py --event "concurrent_calls:2500" --topology full_analytics

# 3. With real utilization data
python predict.py --event "concurrent_calls:2500" --utilization utilization.json --output plan.json
```

## How It Works

1. **Collect** current quota utilization via CloudWatch APIs
2. **Model** the service composition as a dependency graph with measured amplification factors
3. **Predict** which downstream quotas will exhaust using cascade propagation + GNN
4. **Recommend** specific quota increases needed before the planned event

## Measured Amplification (from real AWS infrastructure)

| Downstream Service | Amplification Factor | Method |
|---|---|---|
| DynamoDB Read | 0.50× per invocation | CloudWatch ConsumedReadCapacityUnits |
| DynamoDB Write | 1.00× per invocation | CloudWatch ConsumedWriteCapacityUnits |
| Kinesis Records | 1.00× per invocation | CloudWatch IncomingRecords |
| CloudWatch Puts | 1.00× per invocation | Custom namespace metric count |
| **Total** | **3.50×** | per Lambda invocation |

## Measured Cascade Failure

With Lambda concurrency capped at 5 and 200 concurrent requests:
- **86% throttle rate** — 171 of 200 calls throttled
- **0 downstream operations** for throttled calls (binary failure, not degradation)
- **1,248 operations lost** across DynamoDB, Kinesis, and CloudWatch

## Project Structure

```
src/
├── predict.py                 # End-to-end CLI: collect → predict → recommend
├── collect_utilization.py     # CloudWatch metric collector
├── cascade_model.py           # Core data structures (Quota, Edge, Graph)
├── cascade_engine.py          # Cascade propagation algorithm
├── topologies.py              # 5 configurable service compositions
├── prescaling_recommender.py  # Pre-scaling recommendation engine
├── gnn_model_v2.py            # Graph Attention Network for cascade prediction
└── alibaba_variability_v2.py  # Alibaba trace analysis (fan-out variability)
```

## Topologies

| Topology | Quotas | Services |
|----------|--------|----------|
| basic_voice | 5 | Lambda, DynamoDB, Kinesis, CloudWatch |
| ivr_enabled | 7 | + Lex, Polly |
| full_analytics | 9 | + Transcribe, Comprehend |
| omnichannel | 12 | + Chat, Email, Tasks |
| chat_only | 5 | Lambda, DynamoDB, Lex, CloudWatch |

## Datasets

- **Alibaba Microservice Traces** (Luo et al., SoCC 2021) — 20K+ services, 12 hours production data
- **Measured AWS data** — amplification factors and cascade failures from controlled experiments

## Citation

```bibtex
@article{seeryada2026cascade,
  title={Predicting Cascading Quota Exhaustion in Composed Cloud Services},
  author={Seeryada, Guruprasad},
  journal={arXiv preprint},
  year={2026}
}
```

## License

MIT
