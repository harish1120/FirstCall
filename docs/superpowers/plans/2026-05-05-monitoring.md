# FirstCall Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit custom CloudWatch metrics for active WebSocket connections, transcript latency, OpenAI response time, and error rate per endpoint — plus SNS alarms for error rate > 5% and health check failures.

**Architecture:** A thin `app/metrics.py` module wraps `boto3` CloudWatch PutMetricData and exposes a `put_metric()` function and a `@timer` context manager. All emit calls are fire-and-forget (silently no-op if AWS credentials are absent, so local dev is unaffected). Metrics are emitted inline at each instrumentation point. Alarms and SNS are provisioned via a one-time shell script.

**Tech Stack:** `boto3` (already in dependencies), AWS CloudWatch, AWS SNS, FastAPI middleware

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/metrics.py` | CloudWatch client, `put_metric`, `timed` context manager |
| Modify | `app/main.py` | WebSocket connection counter, error-rate middleware |
| Modify | `app/stt.py` | Transcript latency — time from first audio chunk to `on_transcript` |
| Modify | `app/agent.py` | OpenAI response time around `chat.completions.create` |
| Create | `scripts/setup_alarms.sh` | One-time AWS CLI: SNS topic, subscriptions, alarms |
| Create | `tests/test_metrics.py` | Unit tests for `put_metric` and `timed` |

---

### Task 1: Create `app/metrics.py`

**Files:**
- Create: `app/metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_metrics.py
from unittest.mock import MagicMock, patch
import time

import pytest

import app.metrics as metrics_module
from app.metrics import put_metric, timed


@pytest.fixture(autouse=True)
def mock_cw():
    with patch.object(metrics_module, "_client") as mock:
        yield mock


def test_put_metric_calls_cloudwatch(mock_cw):
    put_metric("TestMetric", 42.0)
    mock_cw.put_metric_data.assert_called_once()
    call_kwargs = mock_cw.put_metric_data.call_args.kwargs
    assert call_kwargs["Namespace"] == "FirstCall"
    assert call_kwargs["MetricData"][0]["MetricName"] == "TestMetric"
    assert call_kwargs["MetricData"][0]["Value"] == 42.0
    assert call_kwargs["MetricData"][0]["Unit"] == "Count"


def test_put_metric_with_dimensions(mock_cw):
    put_metric("TestMetric", 1.0, dimensions={"Endpoint": "/voice"})
    data = mock_cw.put_metric_data.call_args.kwargs["MetricData"][0]
    assert {"Name": "Endpoint", "Value": "/voice"} in data["Dimensions"]


def test_put_metric_swallows_exceptions(mock_cw):
    mock_cw.put_metric_data.side_effect = Exception("no creds")
    put_metric("TestMetric", 1.0)  # must not raise


def test_timed_emits_milliseconds(mock_cw):
    with timed("MyLatency"):
        time.sleep(0.01)
    data = mock_cw.put_metric_data.call_args.kwargs["MetricData"][0]
    assert data["MetricName"] == "MyLatency"
    assert data["Unit"] == "Milliseconds"
    assert data["Value"] >= 10  # at least 10ms
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_metrics.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.metrics'`

- [ ] **Step 3: Create `app/metrics.py`**

```python
import os
from contextlib import contextmanager
from time import perf_counter

import boto3

_client = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "ca-central-1"))
NAMESPACE = "FirstCall"


def put_metric(name: str, value: float, unit: str = "Count", dimensions: dict | None = None) -> None:
    dims = [{"Name": k, "Value": v} for k, v in (dimensions or {}).items()]
    try:
        _client.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit, "Dimensions": dims}],
        )
    except Exception:
        pass


@contextmanager
def timed(name: str, dimensions: dict | None = None):
    start = perf_counter()
    try:
        yield
    finally:
        put_metric(name, (perf_counter() - start) * 1000, unit="Milliseconds", dimensions=dimensions)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_metrics.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/metrics.py tests/test_metrics.py
git commit -m "Add CloudWatch metrics module with put_metric and timed helpers"
```

---

### Task 2: Track Active WebSocket Connections in `app/main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add connection counter and emit on connect/disconnect**

In `app/main.py`, add the import at the top:
```python
from app.metrics import put_metric, timed
```

Replace the `stream` websocket handler opening with:
```python
@app.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    put_metric("ActiveWebSocketConnections", 1)
    try:
        # ... existing handler body unchanged ...
    finally:
        put_metric("ActiveWebSocketConnections", -1)
```

Wrap the entire existing body of `stream` in the `try/finally` block above.

- [ ] **Step 2: Verify app still starts**

```bash
uv run uvicorn app.main:app --port 8000 &
curl -s http://localhost:8000/health
kill %1
```
Expected: `{"status":"ok"}`

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "Emit ActiveWebSocketConnections metric on WebSocket connect/disconnect"
```

---

### Task 3: Add Error Rate Middleware in `app/main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add middleware after app is created**

Add after `app = FastAPI(title="FirstCall")`:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        endpoint = request.url.path
        with timed("EndpointLatency", dimensions={"Endpoint": endpoint}):
            response = await call_next(request)
        put_metric("RequestCount", 1, dimensions={"Endpoint": endpoint})
        if response.status_code >= 500:
            put_metric("ErrorCount", 1, dimensions={"Endpoint": endpoint})
        return response

app.add_middleware(MetricsMiddleware)
```

- [ ] **Step 2: Verify app still starts**

```bash
uv run uvicorn app.main:app --port 8000 &
curl -s http://localhost:8000/health
kill %1
```
Expected: `{"status":"ok"}`

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "Add MetricsMiddleware: emit RequestCount, ErrorCount, EndpointLatency per endpoint"
```

---

### Task 4: Track Transcript Latency in `app/stt.py`

**Files:**
- Modify: `app/stt.py`

- [ ] **Step 1: Record time of first audio chunk and emit latency when transcript fires**

In `app/stt.py`, add import:
```python
from time import perf_counter
from app.metrics import put_metric
```

In `transcribe_stream`, inside `send_audio`, record the first chunk time:
```python
async def send_audio():
    chunks_sent = 0
    first_chunk_time: float | None = None
    while True:
        chunk = await audio_queue.get()
        if chunk is None:
            print(f"[STT] send_audio done — sent {chunks_sent} chunks")
            await connection.send_close_stream(ListenV2CloseStream(type="CloseStream"))
            break
        if chunks_sent == 0:
            first_chunk_time = perf_counter()
            print("[STT] First audio chunk sent to Deepgram")
        chunks_sent += 1
        await connection.send_media(chunk)
```

Then in `on_message`, after the transcript fires condition, emit the latency:
```python
if (
    transcript
    and turn_index not in responded_turns
    and (is_end_of_turn or is_high_confidence_final)
):
    responded_turns.add(turn_index)
    if first_chunk_time is not None:
        latency_ms = (perf_counter() - first_chunk_time) * 1000
        put_metric("TranscriptLatency", latency_ms, unit="Milliseconds")
    print(f"[STT] Firing on_transcript for turn {turn_index}: {repr(transcript)}")
    await on_transcript(transcript)
```

Note: `first_chunk_time` is captured by the closure from `send_audio`.

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
uv run pytest tests/ -v
```
Expected: all passing

- [ ] **Step 3: Commit**

```bash
git add app/stt.py
git commit -m "Emit TranscriptLatency metric: time from first audio chunk to transcript"
```

---

### Task 5: Track OpenAI Response Time in `app/agent.py`

**Files:**
- Modify: `app/agent.py`

- [ ] **Step 1: Wrap the OpenAI call with `timed`**

In `app/agent.py`, add import:
```python
from app.metrics import timed
```

Replace the try block in `build_response`:
```python
    try:
        with timed("OpenAIResponseTime"):
            response = await client.chat.completions.create(
                model="gpt-5.4-mini",
                messages=session["messages"],
            )
        reply = response.choices[0].message.content or ""
    except Exception as e:
        print(f"OpenAI error: {e}")
        reply = "I am having trouble connecting. Please call 911 directly."
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
uv run pytest tests/test_agent.py -v
```
Expected: all passing

- [ ] **Step 3: Commit**

```bash
git add app/agent.py
git commit -m "Emit OpenAIResponseTime metric around chat.completions.create"
```

---

### Task 6: Set Up SNS Alarms (one-time script)

**Files:**
- Create: `scripts/setup_alarms.sh`

- [ ] **Step 1: Create the script**

```bash
#!/bin/bash
# Run once from a machine with AWS CLI configured for ca-central-1.
# Usage: ALERT_EMAIL=you@example.com bash scripts/setup_alarms.sh

set -e
REGION="ca-central-1"
EMAIL="${ALERT_EMAIL:?Set ALERT_EMAIL env var}"

echo "Creating SNS topic..."
TOPIC_ARN=$(aws sns create-topic --name firstcall-alerts --region $REGION --query TopicArn --output text)
echo "Topic: $TOPIC_ARN"

echo "Subscribing $EMAIL..."
aws sns subscribe --topic-arn $TOPIC_ARN --protocol email --notification-endpoint $EMAIL --region $REGION

echo "Alarm: ErrorCount > 5% of RequestCount (using ErrorCount > 10 in 5 min as proxy)..."
aws cloudwatch put-metric-alarm \
  --alarm-name "FirstCall-HighErrorRate" \
  --alarm-description "Error rate elevated" \
  --namespace FirstCall \
  --metric-name ErrorCount \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions $TOPIC_ARN \
  --region $REGION

echo "Alarm: ALB health check failures (no healthy targets)..."
# Replace YOUR_TARGET_GROUP_ARN with your actual target group ARN
TARGET_GROUP_ARN="${TARGET_GROUP_ARN:?Set TARGET_GROUP_ARN env var}"
aws cloudwatch put-metric-alarm \
  --alarm-name "FirstCall-NoHealthyTargets" \
  --alarm-description "/health endpoint not responding" \
  --namespace AWS/ApplicationELB \
  --metric-name HealthyHostCount \
  --dimensions Name=TargetGroup,Value=$(echo $TARGET_GROUP_ARN | cut -d: -f6) \
  --statistic Average \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator LessThanThreshold \
  --alarm-actions $TOPIC_ARN \
  --region $REGION

echo "Done. Check your email to confirm the SNS subscription."
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/setup_alarms.sh
ALERT_EMAIL=harishsunda97@gmail.com TARGET_GROUP_ARN=<your-tg-arn> bash scripts/setup_alarms.sh
```

Get your Target Group ARN from: EC2 → Target Groups → your group → copy the ARN from the details panel.

Check your email — confirm the SNS subscription link.

- [ ] **Step 3: Verify alarms exist in console**

AWS Console → CloudWatch → Alarms — you should see `FirstCall-HighErrorRate` and `FirstCall-NoHealthyTargets`.

- [ ] **Step 4: Commit the script**

```bash
git add scripts/setup_alarms.sh
git commit -m "Add one-time alarm setup script: SNS topic, error rate and health check alarms"
```

---

### Task 7: Grant EC2 Permission to Write CloudWatch Metrics

**No code change — AWS Console only.**

- [ ] **Step 1: Add CloudWatch policy to EC2 role**

1. EC2 → your instance → Security tab → IAM Role → click the role name
2. Attach policy: search `CloudWatchAgentServerPolicy` → attach
3. If you don't have a role attached: EC2 → Actions → Security → Modify IAM role → create/attach one

- [ ] **Step 2: Verify metrics appear after next call**

After deploying and making a test call, go to:
CloudWatch → Metrics → Custom namespaces → `FirstCall`

You should see: `ActiveWebSocketConnections`, `TranscriptLatency`, `OpenAIResponseTime`, `RequestCount`, `ErrorCount`, `EndpointLatency`.

---

### Task 8: Push and Deploy

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -v
```
Expected: all passing

- [ ] **Step 2: Push**

```bash
git push
```

- [ ] **Step 3: Make a test call after deploy**

Check CloudWatch → Metrics → FirstCall namespace to confirm metrics appear.
