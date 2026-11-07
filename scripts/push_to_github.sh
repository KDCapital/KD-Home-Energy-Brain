#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

REMOTE_URL="https://github.com/KDCapital/KD-Home-Energy-Brain.git"

if ! git config user.name >/dev/null 2>&1; then
  git config user.name "KDCapital"
fi
if ! git config user.email >/dev/null 2>&1; then
  git config user.email "KDCapital@users.noreply.github.com"
fi

git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  git commit -m "feat: HEMS Master integration, dashboard and tests"
fi

git branch -M main
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi
git push -u origin main
