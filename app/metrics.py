import os

import boto3

from app.logger import get_logger

logger = get_logger("metrics")


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "ca-central-1"))
    return _client


def put_metric(name: str, value: float, unit: str = "Count", dimensions: dict | None = None):
    if os.getenv("APP_ENV") != "production":
        return

    try:
        dims = [{"Name": k, "Value": v} for k, v in (dimensions or {}).items()]
        _get_client().put_metric_data(
            Namespace="FirstCall",
            MetricData=[
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": dims,
                }
            ],
        )
    except Exception as e:
        logger.error("CloudWatch metric failed", extra={"metric": name, "error": str(e)})
