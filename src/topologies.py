"""
Contact Center Topologies
=========================
Configurable service compositions representing different contact center setups.
All quota limits from publicly documented AWS/Azure/GCP service limits.
"""

from cascade_model import CascadeGraph, Severity


def topology_basic_voice() -> CascadeGraph:
    """
    Basic voice-only contact center.
    No IVR bot, no transcription, no analytics.
    Minimal peripherals: Lambda (routing) + DynamoDB (state) + Kinesis (CTR) + S3 (recordings)
    """
    g = CascadeGraph()

    # Core
    g.add_quota("concurrent_calls", 5000, 3500, Severity.FATAL, "Connect", 72)
    g.add_quota("lambda_concurrency", 1000, 550, Severity.FATAL, "Lambda", 4)
    g.add_quota("dynamodb_wcu", 25000, 12000, Severity.FATAL, "DynamoDB", 1)
    g.add_quota("kinesis_records_sec", 1000, 400, Severity.COMPLIANCE, "Kinesis", 0.1)
    g.add_quota("cloudwatch_put_tps", 500, 180, Severity.COSMETIC, "CloudWatch", 24)

    # Edges: each call triggers Lambda for contact flow
    g.add_edge("concurrent_calls", "lambda_concurrency", 0.8, threshold=0.6)
    g.add_edge("concurrent_calls", "dynamodb_wcu", 2.5, threshold=0.5)
    g.add_edge("concurrent_calls", "kinesis_records_sec", 0.4, threshold=0.3)
    g.add_edge("lambda_concurrency", "cloudwatch_put_tps", 0.2, threshold=0.8)
    g.add_edge("lambda_concurrency", "dynamodb_wcu", 1.5, threshold=0.9)  # retries hit DB

    return g


def topology_ivr_enabled() -> CascadeGraph:
    """
    Voice + IVR (Lex bot) contact center.
    Adds Lex concurrent sessions as a dependency.
    """
    g = topology_basic_voice()

    g.add_quota("lex_concurrent", 200, 85, Severity.DEGRADED, "Lex", 48)
    g.add_quota("polly_requests_sec", 80, 30, Severity.DEGRADED, "Polly", 24)

    # ~60% of calls hit IVR
    g.add_edge("concurrent_calls", "lex_concurrent", 0.6, threshold=0.5)
    g.add_edge("lex_concurrent", "lambda_concurrency", 0.3, threshold=0.7)  # Lex fulfillment
    g.add_edge("lex_concurrent", "polly_requests_sec", 0.4, threshold=0.5)

    return g


def topology_full_analytics() -> CascadeGraph:
    """
    Full-featured: Voice + IVR + Contact Lens (transcription + sentiment).
    Maximum peripheral dependencies.
    """
    g = topology_ivr_enabled()

    g.add_quota("transcribe_streams", 200, 70, Severity.DEGRADED, "Transcribe", 48)
    g.add_quota("comprehend_chars_sec", 100000, 35000, Severity.DEGRADED, "Comprehend", 48)
    g.add_quota("sqs_messages_sec", 50000, 15000, Severity.FATAL, "SQS", 1)

    # Contact Lens: every call gets transcribed + analyzed
    g.add_edge("concurrent_calls", "transcribe_streams", 0.9, threshold=0.4)
    g.add_edge("transcribe_streams", "comprehend_chars_sec", 500, threshold=0.6)
    # Overflow queue
    g.add_edge("concurrent_calls", "sqs_messages_sec", 1.5, threshold=0.9)
    g.add_edge("sqs_messages_sec", "lambda_concurrency", 0.3, threshold=0.85)

    return g


def topology_omnichannel() -> CascadeGraph:
    """
    Omnichannel: Voice + Chat + Email + Tasks.
    Multiple entry points sharing downstream resources.
    """
    g = topology_full_analytics()

    g.add_quota("concurrent_chats", 5000, 2200, Severity.FATAL, "Connect", 72)
    g.add_quota("concurrent_emails", 1000, 300, Severity.FATAL, "Connect", 72)
    g.add_quota("concurrent_tasks", 2500, 800, Severity.DEGRADED, "Connect", 24)

    # Chats also consume Lambda + DynamoDB
    g.add_edge("concurrent_chats", "lambda_concurrency", 0.5, threshold=0.6)
    g.add_edge("concurrent_chats", "dynamodb_wcu", 2.0, threshold=0.5)
    g.add_edge("concurrent_chats", "lex_concurrent", 0.7, threshold=0.5)  # chatbots
    # Emails trigger tasks
    g.add_edge("concurrent_emails", "concurrent_tasks", 0.3, threshold=0.7)
    g.add_edge("concurrent_emails", "lambda_concurrency", 0.2, threshold=0.6)

    return g


def topology_minimal_chat() -> CascadeGraph:
    """
    Chat-only contact center. No voice, no analytics.
    Simpler graph but different cascade patterns.
    """
    g = CascadeGraph()

    g.add_quota("concurrent_chats", 5000, 3000, Severity.FATAL, "Connect", 72)
    g.add_quota("lambda_concurrency", 1000, 400, Severity.FATAL, "Lambda", 4)
    g.add_quota("dynamodb_wcu", 25000, 10000, Severity.FATAL, "DynamoDB", 1)
    g.add_quota("lex_concurrent", 200, 120, Severity.DEGRADED, "Lex", 48)
    g.add_quota("cloudwatch_put_tps", 500, 150, Severity.COSMETIC, "CloudWatch", 24)

    g.add_edge("concurrent_chats", "lambda_concurrency", 0.6, threshold=0.6)
    g.add_edge("concurrent_chats", "dynamodb_wcu", 3.0, threshold=0.5)
    g.add_edge("concurrent_chats", "lex_concurrent", 0.8, threshold=0.5)
    g.add_edge("lambda_concurrency", "dynamodb_wcu", 1.5, threshold=0.9)
    g.add_edge("lambda_concurrency", "cloudwatch_put_tps", 0.2, threshold=0.8)
    g.add_edge("lex_concurrent", "lambda_concurrency", 0.3, threshold=0.8)

    return g


ALL_TOPOLOGIES = {
    "basic_voice": topology_basic_voice,
    "ivr_enabled": topology_ivr_enabled,
    "full_analytics": topology_full_analytics,
    "omnichannel": topology_omnichannel,
    "minimal_chat": topology_minimal_chat,
}
