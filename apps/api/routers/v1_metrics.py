"""
MEMORA v1 Metrics & Observability Endpoints
Provides JSON and Prometheus telemetry for SLI monitoring.
"""
from fastapi import APIRouter, Response

from core.metrics.collector import metrics_collector

router = APIRouter(tags=["v1 Observability & Metrics"])

@router.get("/v1/metrics")
def get_metrics_json():
    """Returns JSON metrics summary for MEMORA infrastructure."""
    return metrics_collector.get_metrics_summary()

@router.get("/metrics")
def get_prometheus_metrics():
    """Returns standard Prometheus formatted metrics."""
    content = metrics_collector.get_prometheus_format()
    return Response(content=content, media_type="text/plain; version=0.0.4")
