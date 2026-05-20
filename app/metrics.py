import asyncio
import os

import boto3

from app.logger import get_logger

logger = get_logger("metrics")


_client = None
_metrics_queue: asyncio.Queue | None = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "ca-central-1"))
    return _client


def get_metrics_queue() -> asyncio.Queue:
    global _metrics_queue
    if _metrics_queue is None:
        _metrics_queue = asyncio.Queue()
    return _metrics_queue


async def metrics_worker():
    queue = get_metrics_queue()

    while True:
        item = await queue.get()
        batch = [item]
        while not queue.empty() and len(batch) < 20:
            batch.append(queue.get_nowait())

        metric_data = []

        for name, value, unit, dimensions in batch:
            dims = [{"Name": k, "Value": v} for k, v in (dimensions or {}).items()]
            metric_data.append(
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": dims,
                }
            )

        try:
            await asyncio.to_thread(
                _get_client().put_metric_data,
                Namespace="FirstCall",
                MetricData=metric_data,
            )
        except Exception as e:
            logger.error("CloudWatch batch failed", extra={"error": str(e)})


def put_metric(name: str, value: float, unit: str = "Count", dimensions: dict | None = None):
    if os.getenv("APP_ENV") != "production":
        return

    try:
        get_metrics_queue().put_nowait((name, value, unit, dimensions))
    except asyncio.QueueFull:
        logger.error("Metrics queue full, dropping metric", extra={"metric": name})
