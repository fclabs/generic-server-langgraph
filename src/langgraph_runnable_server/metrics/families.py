"""Prometheus runnable metric families (spec 02, FR-120–FR-123, BR-201–BR-203)."""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram

BR_202_DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _full_metric_name(namespace: str, base: str) -> str:
    return f"{namespace}_{base}" if namespace else base


@dataclass(frozen=True, slots=True)
class MetricFamilies:
    """The five runnable counters/histograms bound to one ``CollectorRegistry``."""

    requests_total: Counter
    request_duration_seconds: Histogram
    errors_total: Counter
    request_size_bytes: Histogram
    response_size_bytes: Histogram


def build_metrics(namespace: str, registry: CollectorRegistry) -> MetricFamilies:
    """Register the five runnable metric families on ``registry`` and return handles (FR-120)."""

    def name(base: str) -> str:
        return _full_metric_name(namespace, base)

    labelnames = ("runnable", "endpoint")
    error_labelnames = ("runnable", "endpoint", "http_status_class")

    requests_total = Counter(
        name("requests_total"),
        "Total HTTP requests handled by runnable invoke/batch routes.",
        labelnames=labelnames,
        registry=registry,
    )
    request_duration_seconds = Histogram(
        name("request_duration_seconds"),
        "Wall time for runnable invoke/batch requests (seconds).",
        labelnames=labelnames,
        buckets=BR_202_DURATION_BUCKETS,
        registry=registry,
    )
    errors_total = Counter(
        name("errors_total"),
        "Total HTTP responses with 4xx or 5xx status from runnable routes.",
        labelnames=error_labelnames,
        registry=registry,
    )
    request_size_bytes = Histogram(
        name("request_size_bytes"),
        "Runnable request body size (bytes); omitted when BR-203 says unknown.",
        labelnames=labelnames,
        registry=registry,
    )
    response_size_bytes = Histogram(
        name("response_size_bytes"),
        "Runnable response body size (bytes).",
        labelnames=labelnames,
        registry=registry,
    )
    return MetricFamilies(
        requests_total=requests_total,
        request_duration_seconds=request_duration_seconds,
        errors_total=errors_total,
        request_size_bytes=request_size_bytes,
        response_size_bytes=response_size_bytes,
    )
