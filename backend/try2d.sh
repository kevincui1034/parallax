#!/usr/bin/env bash
# Run the 2D pipeline end-to-end: photo -> GPT-Image-2-Edit -> Kling V3 video.
# Usage:  ./try2d.sh /path/to/photo.jpg
# Env:    API_BASE (default http://localhost:8000)
set -euo pipefail

IMG="${1:-}"
API="${API_BASE:-http://localhost:8000}"

if [[ -z "$IMG" || ! -f "$IMG" ]]; then
  echo "usage: ./try2d.sh /path/to/photo.jpg" >&2
  exit 1
fi

echo "→ submitting $IMG to $API (mode=2d) …"
JOB=$(curl -s -X POST "$API/api/generate" -F "image=@${IMG}" -F "mode=2d" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
echo "  job_id: $JOB"

echo "→ polling (image gen + Kling can take a few minutes) …"
while true; do
  J=$(curl -s "$API/api/jobs/$JOB")
  read -r STATUS PROGRESS <<<"$(echo "$J" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['status'], d.get('progress',0))")"
  printf "\r  status=%-9s progress=%s%%   " "$STATUS" "$PROGRESS"
  if [[ "$STATUS" == "done" || "$STATUS" == "error" ]]; then echo; break; fi
  sleep 5
done

if [[ "$STATUS" == "error" ]]; then
  echo "✗ failed:"
  echo "$J" | python3 -c "import sys,json;print('  '+ (json.load(sys.stdin).get('error') or 'unknown error'))"
  exit 1
fi

VIDEO=$(echo "$J" | python3 -c "import sys,json;print((json.load(sys.stdin).get('result') or {}).get('video_url',''))")
echo "✓ exploded-view video:"
echo "  $VIDEO"
# open it (macOS); harmless elsewhere
command -v open >/dev/null && [[ -n "$VIDEO" ]] && open "$VIDEO" || true
