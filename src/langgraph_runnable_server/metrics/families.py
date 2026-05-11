"""Prometheus metric families for the runnable HTTP surface (spec 02, iteration 4).

Registers five families on a caller-supplied :class:`prometheus_client.registry.CollectorRegistry`
(never the process-default ``REGISTRY``):

* ``requests_total`` — labels ``runnable``, ``endpoint`` (``invoke`` | ``batch``).
* ``request_duration_seconds`` — same labels; histogram with **BR-202** buckets
  ``(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)`` plus ``+Inf``.
* ``errors_total`` — labels ``runnable``, ``endpoint``, ``http_status_class`` (``4xx`` | ``5xx``).
* ``request_size_bytes`` — same labels as duration; request-body size histogram (BR-203 omit rules
  applied in middleware/handlers, not here).
* ``response_size_bytes`` — same; response-body size histogram.

Metric **names** in exposition: if ``namespace`` is empty, names are the bases above; otherwise
``{namespace}_{base}`` (explicit composition — no ``prometheus_client`` ``namespace=`` argument
for the empty case).
"""

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
    """Handles to the five runnable metric objects (all bound to one registry)."""

    requests_total: Counter
    request_duration_seconds: Histogram
    errors_total: Counter
    request_size_bytes: Histogram
    response_size_bytes: Histogram


def build_metrics(namespace: str, registry: CollectorRegistry) -> MetricFamilies:
    """Register the five runnable metric families on ``registry`` and return handles.

    ``namespace`` must already satisfy FR-123 (empty or identifier); empty means no name prefix.
    """

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
