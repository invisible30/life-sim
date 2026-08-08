#!/usr/bin/env bash
# Commit-msg hook: enforce Conventional Commits format.
# Install: cp scripts/commit-msg.sh .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg

set -e

commit_msg_file="$1"
commit_msg=$(head -n1 "$commit_msg_file")

# Allow merge / revert / fixup / squash commits to bypass
if echo "$commit_msg" | grep -qE '^(Merge|Revert|fixup!|squash!|amend!)'; then
  exit 0
fi

# Conventional Commits regex (simplified, single-line subject)
pattern='^(feat|fix|docs|style|refactor|perf|test|chore|build|ci)(\([a-z0-9_-]+\))?!?: .{1,72}$'

if ! echo "$commit_msg" | grep -qE "$pattern"; then
  echo ""
  echo "❌ Commit message does not follow Conventional Commits."
  echo ""
  echo "   Got:      $commit_msg"
  echo ""
  echo "   Expected: <type>(<scope>): <subject>"
  echo ""
  echo "   Types:    feat | fix | docs | style | refactor | perf | test | chore | build | ci"
  echo "   Scopes:   core | agents | meeting | llm | output | data | tests | config | docs"
  echo "   Example:  feat(agents): add rational persona with ROI heuristics"
  echo ""
  exit 1
fi
