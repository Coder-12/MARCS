#!/usr/bin/env bash
#
# Send demo GitHub-style webhooks to MARCS API.
# Works on macOS/Linux.
#

set -e

URL="http://localhost:8000/webhook"

echo ""
echo "=============================================="
echo "     MARCS Webhook Demo Sender (Localhost)     "
echo "=============================================="
echo ""

send() {
  EVENT="$1"
  PAYLOAD="$2"

  echo ""
  echo "---- Sending $EVENT ----"
  curl -s -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "X-GitHub-Event: $EVENT" \
    -H "X-GitHub-Delivery: demo-$(date +%s)" \
    -d "$PAYLOAD" | jq
  echo "---- DONE ($EVENT) ----"
  echo ""
}

# ---------------------------------------
# 1️⃣ Ping
# ---------------------------------------
send "ping" '{"zen": "Keep it logically awesome."}'

# ---------------------------------------
# 2️⃣ Push event
# ---------------------------------------
send "push" '{
  "ref": "refs/heads/main",
  "repository": {
    "name": "demo-repo",
    "full_name": "aklesh/demo-repo"
  },
  "head_commit": {
    "id": "abcdef123456",
    "message": "demo commit"
  }
}'

# ---------------------------------------
# 3️⃣ Pull Request
# ---------------------------------------
send "pull_request" '{
  "action": "opened",
  "number": 7,
  "pull_request": {
    "id": 999,
    "title": "Demo PR from webhook script",
    "head": { "ref": "demo-branch" },
    "base": { "ref": "main" }
  },
  "repository": {
    "name": "demo-repo",
    "full_name": "aklesh/demo-repo"
  }
}'

echo ""
echo "=============================================="
echo "      All demo webhook events sent ✔️"
echo "=============================================="
echo ""