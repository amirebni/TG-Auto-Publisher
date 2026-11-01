#!/usr/bin/env bash
# Keeps data/state.sqlite3 on an orphan "state" branch between workflow runs.
#   scripts/state.sh restore   (before the publisher runs)
#   scripts/state.sh save      (after it runs, even when it failed)
set -euo pipefail

DB="data/state.sqlite3"
BRANCH="state"
HASH_FILE="${RUNNER_TEMP:-/tmp}/state.orig.sha"

case "${1:-}" in

restore)
  mkdir -p data
  rm -f "$HASH_FILE"

  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git fetch --quiet --depth=1 origin "$BRANCH"

    if git show "FETCH_HEAD:state.sqlite3" > "$DB.tmp" 2>/dev/null; then
      mv "$DB.tmp" "$DB"
      sha256sum "$DB" | cut -d' ' -f1 > "$HASH_FILE"
      echo "State restored."
    else
      rm -f "$DB.tmp"
      echo "State branch exists but has no state file."
    fi
  else
    echo "No state branch yet (first run)."
  fi
  ;;

save)
  if [ ! -f "$DB" ]; then
    echo "No state file to save."
    exit 0
  fi

  new_hash=$(sha256sum "$DB" | cut -d' ' -f1)

  if [ -f "$HASH_FILE" ] && [ "$new_hash" = "$(cat "$HASH_FILE")" ]; then
    echo "State unchanged - nothing to push."
    exit 0
  fi

  remote="${STATE_REMOTE:-$(git remote get-url origin)}"
  work=$(mktemp -d)
  cp "$DB" "$work/state.sqlite3"

  cd "$work"
  git init -q -b "$BRANCH"
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git add -f state.sqlite3
  git commit -q -m "Update publisher state"
  git remote add origin "$remote"

  for attempt in 1 2 3; do
    if git push -q --force origin "$BRANCH"; then
      echo "State saved."
      exit 0
    fi
    echo "Push failed (attempt $attempt), retrying..."
    sleep $((attempt * 3))
  done

  echo "ERROR: could not save state" >&2
  exit 1
  ;;

*)
  echo "usage: $0 restore|save" >&2
  exit 2
  ;;
esac
