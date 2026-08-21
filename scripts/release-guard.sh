#!/usr/bin/env bash
set -euo pipefail

# Run before copying the final application package into the repository.
# This script intentionally does NOT upload or commit application code.

forbidden='(^|/)\.env($|\.)|(^|/)(credentials|secrets)[^/]*\.(json|ya?ml|txt)$'
if git ls-files -z | grep -zE "$forbidden" >/dev/null; then
  echo "ERROR: tracked secret-bearing file found."
  exit 1
fi

if find . -type f \( -name '.env' -o -name '.env.*' \) ! -name '.env.example' -print -quit | grep -q .; then
  echo "ERROR: local secret/config file found in release tree."
  exit 1
fi

echo "Release guard: OK. No local secret/config file was detected."
