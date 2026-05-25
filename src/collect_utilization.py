"""
collect_utilization.py — CloudWatch Quota Utilization Collector
===============================================================
Pulls current quota utilization AND actual applied limits for all services
in a contact center composition from CloudWatch and Service Quotas APIs.

Usage:
    python collect_utilization.py --region us-east-1 --profile default

Output: JSON file with current utilization AND real limits per quota.
"""

import boto3
import json
import argparse
from datetime import datetime, timezone, timedelta
from typing import Dict


# Quota limit codes (from AWS Service Quotas)
QUOTA_LIMITS = {
    "lambda_concurrency": {"service_code": "lambda", "quota_code": "L-B99A9384"},
    "dynamodb_table_rcu": {"service_code": "dynamodb", "quota_code": "L-C8B9BFAB"},
    "dynamodb_table_wcu": {"service_code": "dynamodb", "quota_code": "L-AD8F5CE5"},
    "kinesis_shards": {"service_code": "kinesis", "quota_code": "L-5765E70F"},
    "connect_concurrent_calls": {"service_code": "connect", "quota_code": "L-4A5FE8DE"},
}


# Service quota definitions with their CloudWatch metric mappings
QUOTA_METRICS = {
    "lambda_concurrency": {
        "namespace": "AWS/Lambda",
        "metric": "ConcurrentExecutions",
        "statistic": "Maximum",
        "dimensions": [],  # Account-level
        "description": "Lambda concurrent executions (account-level)"
    },
    "lambda_throttles": {
        "namespace": "AWS/Lambda",
        "metric": "Throttles",
        "statistic": "Sum",
        "dimensions": [],
        "description": "Lambda throttle events (account-level)"
    },
    "dynamodb_wcu": {
        "namespace": "AWS/DynamoDB",
        "metric": "ConsumedWriteCapacityUnits",
        "statistic": "Sum",
        "dimensions_key": "TableName",
        "description": "DynamoDB consumed write capacity"
    },
    "dynamodb_rcu": {
        "namespace": "AWS/DynamoDB",
        "metric": "ConsumedReadCapacityUnits",
        "statistic": "Sum",
        "dimensions_key": "TableName",
        "description": "DynamoDB consumed read capacity"
    },
    "kinesis_incoming_records": {
        "namespace": "AWS/Kinesis",
        "metric": "IncomingRecords",
        "statistic": "Sum",
        "dimensions_key": "StreamName",
        "description": "Kinesis incoming records per period"
    },
    "kinesis_write_throughput": {
        "namespace": "AWS/Kinesis",
        "metric": "IncomingBytes",
        "statistic": "Sum",
        "dimensions_key": "StreamName",
        "description": "Kinesis incoming bytes per period"
    },
    "cloudwatch_put_metric": {
        "namespace": "AWS/Usage",
        "metric": "CallCount",
        "statistic": "Sum",
        "dimensions": [
            {"Name": "Type", "Value": "API"},
            {"Name": "Resource", "Value": "PutMetricData"},
            {"Name": "Service", "Value": "CloudWatch"},
            {"Name": "Class", "Value": "None"}
        ],
        "description": "CloudWatch PutMetricData API calls"
    },
}


def collect_metric(cw_client, metric_def: Dict, period_minutes: int = 5,
                   resource_name: str = None) -> float:
    """Pull a single metric value from CloudWatch."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=period_minutes)

    dimensions = metric_def.get("dimensions", [])
    if resource_name and "dimensions_key" in metric_def:
        dimensions = [{"Name": metric_def["dimensions_key"], "Value": resource_name}]

    try:
        resp = cw_client.get_metric_statistics(
            Namespace=metric_def["namespace"],
            MetricName=metric_def["metric"],
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=period_minutes * 60,
            Statistics=[metric_def["statistic"]]
        )

        datapoints = resp.get("Datapoints", [])
        if datapoints:
            return datapoints[0][metric_def["statistic"]]
        return 0.0
    except Exception as e:
        print(f"  Warning: Failed to collect {metric_def['metric']}: {e}")
        return -1.0


def discover_limits(region: str, profile: str = None) -> Dict:
    """
    Query Service Quotas API to get ACTUAL applied limits (not defaults).
    Returns {quota_name: applied_limit_value}.
    """
    session_kwargs = {"region_name": region}
    if profile:
        session_kwargs["profile_name"] = profile

    session = boto3.Session(**session_kwargs)
    sq = session.client("service-quotas")

    limits = {}
    for quota_name, info in QUOTA_LIMITS.items():
        try:
            resp = sq.get_service_quota(
                ServiceCode=info["service_code"],
                QuotaCode=info["quota_code"]
            )
            limits[quota_name] = {
                "value": resp["Quota"]["Value"],
                "name": resp["Quota"]["QuotaName"],
                "adjustable": resp["Quota"].get("Adjustable", True)
            }
        except Exception as e:
            # Fallback: try get_aws_default_service_quota
            try:
                resp = sq.get_aws_default_service_quota(
                    ServiceCode=info["service_code"],
                    QuotaCode=info["quota_code"]
                )
                limits[quota_name] = {
                    "value": resp["Quota"]["Value"],
                    "name": resp["Quota"]["QuotaName"],
                    "adjustable": resp["Quota"].get("Adjustable", True),
                    "is_default": True
                }
            except Exception:
                limits[quota_name] = {"value": -1, "error": str(e)[:80]}

    return limits


def collect_all(region: str, profile: str = None,
                resources: Dict = None) -> Dict:
    """
    Collect utilization for all configured quotas.

    Args:
        region: AWS region
        profile: AWS CLI profile name (optional)
        resources: Dict mapping quota names to resource names
                   e.g., {"dynamodb_wcu": "MyTable", "kinesis_incoming_records": "MyStream"}
    """
    session_kwargs = {"region_name": region}
    if profile:
        session_kwargs["profile_name"] = profile

    session = boto3.Session(**session_kwargs)
    cw = session.client("cloudwatch")

    resources = resources or {}

    print(f"Collecting quota utilization from CloudWatch...")
    print(f"  Region: {region}")
    print(f"  Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Resources: {resources}")
    print()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "quotas": {},
        "limits": {}
    }

    # Discover actual applied limits
    print("  Discovering actual quota limits...")
    results["limits"] = discover_limits(region, profile)
    for name, info in results["limits"].items():
        if info.get("value", -1) > 0:
            default_flag = " (default)" if info.get("is_default") else ""
            print(f"    {name}: {info['value']:.0f}{default_flag}")
        else:
            print(f"    {name}: failed to retrieve")
    print()

    for quota_name, metric_def in QUOTA_METRICS.items():
        resource_name = resources.get(quota_name)

        # Skip resource-specific metrics if no resource specified
        if "dimensions_key" in metric_def and not resource_name:
            continue

        value = collect_metric(cw, metric_def, resource_name=resource_name)
        results["quotas"][quota_name] = {
            "value": value,
            "description": metric_def["description"],
            "resource": resource_name
        }
        status = "✓" if value >= 0 else "✗"
        print(f"  {status} {quota_name}: {value:.1f} ({metric_def['description']})")

    return results


def main():
    parser = argparse.ArgumentParser(description="Collect CloudWatch quota utilization")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--profile", default=None, help="AWS CLI profile")
    parser.add_argument("--output", default="utilization.json", help="Output JSON file")
    parser.add_argument("--table", default=None, help="DynamoDB table name to monitor")
    parser.add_argument("--stream", default=None, help="Kinesis stream name to monitor")
    args = parser.parse_args()

    resources = {}
    if args.table:
        resources["dynamodb_wcu"] = args.table
        resources["dynamodb_rcu"] = args.table
    if args.stream:
        resources["kinesis_incoming_records"] = args.stream
        resources["kinesis_write_throughput"] = args.stream

    results = collect_all(args.region, args.profile, resources)

    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved to {args.output}")


if __name__ == "__main__":
    main()
