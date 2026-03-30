#!/usr/bin/env bash
# PreToolUse hook for Read: block reading corrupt/empty/missing image files.
# Prevents "Could not process image" API 400 errors that poison the context —
# once a bad image enters the conversation, EVERY subsequent message triggers
# the same 400 error until the session is discarded or /compact clears it.
#
# Checks (in order):
#   1. File exists
#   2. File > 100 bytes (rules out truncated transfers)
#   3. PIL can open AND fully decode the pixel data (catches corrupt content)
#   4. Fallback: `file` command confirms MIME is image/*

set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ -z "$FILE_PATH" ] && exit 0

# Only check image extensions
echo "$FILE_PATH" | grep -qiE '\.(png|jpe?g|webp|gif|bmp|tiff?|avif)$' || exit 0

# --- Check 1: existence ---
if [ ! -e "$FILE_PATH" ]; then
  cat <<EOJSON
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: Image file does not exist: $FILE_PATH. If you started a background download (scp/rsync), wait for the task completion notification before reading."}}
EOJSON
  exit 0
fi

# --- Check 2: minimum size (100 bytes) ---
FILE_SIZE=$(stat -f%z "$FILE_PATH" 2>/dev/null || stat -c%s "$FILE_PATH" 2>/dev/null || echo "0")
if [ "$FILE_SIZE" -lt 100 ]; then
  cat <<EOJSON
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: Image file is only ${FILE_SIZE} bytes (< 100): $FILE_PATH. Likely still transferring or corrupt. Wait for download to finish, then retry."}}
EOJSON
  exit 0
fi

# --- Check 3: PIL decode (most reliable) ---
PIL_RESULT=$(python3 -c "
from PIL import Image
try:
    img = Image.open('$FILE_PATH')
    img.load()          # force full pixel decode
    w, h = img.size
    if w < 2 or h < 2:
        print('TOO_SMALL')
    else:
        print('OK')
except Exception as e:
    print('FAIL:' + str(e)[:200])
" 2>/dev/null || echo "NO_PIL")

case "$PIL_RESULT" in
  OK)
    exit 0
    ;;
  TOO_SMALL)
    cat <<EOJSON
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: Image dimensions too small to be valid: $FILE_PATH. Likely a corrupt or placeholder file."}}
EOJSON
    exit 0
    ;;
  NO_PIL)
    # PIL not available — fall through to `file` command check
    ;;
  FAIL:*)
    REASON="${PIL_RESULT#FAIL:}"
    cat <<EOJSON
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: Image is corrupt or unreadable: $FILE_PATH — $REASON. This file would cause 'Could not process image' API errors that poison the entire session. Re-download or use a different file."}}
EOJSON
    exit 0
    ;;
esac

# --- Check 4: fallback — `file` MIME check ---
MIME=$(file --brief --mime-type "$FILE_PATH" 2>/dev/null || echo "unknown")
if ! echo "$MIME" | grep -q '^image/'; then
  cat <<EOJSON
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: File is not a valid image (detected: $MIME): $FILE_PATH. This would cause 'Could not process image' API errors. Re-download or convert the file."}}
EOJSON
  exit 0
fi

exit 0
