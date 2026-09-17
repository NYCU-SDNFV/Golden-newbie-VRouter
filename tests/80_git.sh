#!/bin/sh
# Check G - basic git hygiene. Do not modify.
. "$(dirname "$0")/lib.sh"
banner "check G: git hygiene"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "this is not a git working tree" "run the checks from the repository root"

# 5a - at least 3 commits of your own on top of the starter code.
# Baseline = the Classroom 50 accept commit (the one that added
# .classroom50.yaml); it is student-authored, so counting by author is
# unreliable. Without an accept commit (e.g. instructor PoC) fall back to
# "everything above the root commit".
ACCEPT=$(git log --diff-filter=A --format=%H -- .classroom50.yaml 2>/dev/null | tail -1)
if [ -n "$ACCEPT" ]; then
  MINE=$(git rev-list --count "$ACCEPT"..HEAD 2>/dev/null || echo 0)
else
  TOTAL=$(git rev-list --count HEAD 2>/dev/null || echo 0)
  MINE=$((TOTAL - 1))
fi
[ "$MINE" -lt 0 ] && MINE=0
if [ "$MINE" -lt 3 ]; then
  die "found only $MINE commit(s) on top of the starter code, need at least 3" \
      "commit as you work instead of one big dump at the end"
fi
pass "$MINE commits on top of the starter code"

# 5b - a .gitignore exists and is tracked
git ls-files --error-unmatch .gitignore >/dev/null 2>&1 \
  || die ".gitignore is missing or untracked" \
         "add one and commit it; python and docker both leave litter"
pass ".gitignore is tracked"

# 5c - no build litter committed
JUNK=$(git ls-files | grep -E '(^|/)(__pycache__/|\.env$)|\.pyc$|\.log$' || true)
if [ -n "$JUNK" ]; then
  fail "these files should not be in git:"
  printf '        %s\n' $JUNK >&2
  printf '      hint: git rm --cached <file>, then extend .gitignore\n' >&2
  exit 1
fi
pass "no compiled or generated files are committed"
