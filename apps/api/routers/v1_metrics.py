"""
MEMORA v1 Metrics & Observability Endpoints
Provides JSON and Prometheus telemetry for SLI monitoring.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from core.metrics.collector import metrics_collector

router = APIRouter(tags=["v1 Observability & Metrics"])

@router.get("/v1/metrics")
def get_metrics_json():
    """Returns JSON metrics summary for MEMORA infrastructure."""
    return metrics_collector.get_metrics_summary()

@router.get("/metrics")
def get_prometheus_metrics(request: Request, format: str = None):
    """Returns standard Prometheus formatted metrics, or JSON if requested."""
    accept = request.headers.get("accept", "")
    if (format and format.lower() == "json") or ("application/json" in accept and "text/plain" not in accept):
        return JSONResponse(content=metrics_collector.get_metrics_summary())
    content = metrics_collector.get_prometheus_format()
    return Response(content=content, media_type="text/plain; version=0.0.4")
