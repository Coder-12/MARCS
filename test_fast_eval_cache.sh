#!/usr/bin/env bash

set -euo pipefail

EVENT_ID="auto_style_281a75312b86"
PAYLOAD_FILE="data/golden/v1/${EVENT_ID}.json"

echo "============================================="
echo "   FAST EVAL CACHE AUTOMATED TEST"
echo "============================================="

if [ ! -f "$PAYLOAD_FILE" ]; then
    echo "[FAIL] Missing golden payload file: $PAYLOAD_FILE"
    exit 1
fi

echo "[STEP 1] Reload golden registry…"
curl -s -X POST http://localhost:8001/eval/golden/reload > /dev/null

echo "[STEP 2] Clear FastEval cache…"
curl -s -X POST http://localhost:8001/eval/cache/clear > /dev/null

echo "[STEP 3] Send webhook for EVENT_ID=$EVENT_ID…"
curl -s -X POST http://localhost:8001/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-GitHub-Delivery: ${EVENT_ID}" \
  -d "$(jq -c .payload $PAYLOAD_FILE)" > /dev/null

echo "[STEP 4] Waiting 1.5s for worker to process…"
sleep 1.5

echo "[STEP 5] First evaluation (expect MISS)…"
resp1=$(curl -s http://localhost:8001/eval/review/${EVENT_ID})
echo "$resp1" | jq .

if echo "$resp1" | grep -q '"from_cache": true'; then
    echo "[FAIL] First request should NOT come from cache."
    exit 1
else
    echo "[PASS] First request MISS ✓"
fi

echo "---------------------------------------------"

echo "[STEP 6] Second evaluation (expect HIT)…"
resp2=$(curl -s http://localhost:8001/eval/review/${EVENT_ID})
echo "$resp2" | jq .

if echo "$resp2" | grep -q '"from_cache": true'; then
    echo "[PASS] Second request HIT ✓"
else
    echo "[FAIL] Second request was not served from cache."
    exit 1
fi

echo "---------------------------------------------"
echo "🔥 FAST EVAL CACHE TEST COMPLETED SUCCESSFULLY ✓"
echo "---------------------------------------------"