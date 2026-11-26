#!/usr/bin/env bash
# Full check before every merge. Usage: tools/verify.sh [--submit] [--seed N]
#   KIT_DIR  the organizers' toolkit (default: ../Secret-Wards-ToolKit next to this repo)
#   PY       Python used to run the pipeline (default: python3)
set -u
cd "$(dirname "$0")/.."
REPO="$(pwd)"
KIT="${KIT_DIR:-$REPO/../Secret-Wards-ToolKit}"
PY="${PY:-python3}"
TEST_PY="$REPO/.venv/bin/python"; [ -x "$TEST_PY" ] || TEST_PY="$PY"
SEED=1; SUBMIT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --submit) SUBMIT=1 ;;
    --seed) SEED="$2"; shift ;;
  esac
  shift
done

# Outside the repo, so test files (such as the scrambled PDF) never look like submission files.
WORK="${VERIFY_DIR:-${TMPDIR:-/tmp}/rakshawall-verify}"
rm -rf "$WORK"; mkdir -p "$WORK"
FAILS=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; FAILS=$((FAILS + 1)); }
warn() { echo "  WARN  $1"; }

SECRET_A="verify-a-$RANDOM$RANDOM"
SECRET_B="verify-b-$RANDOM$RANDOM"

# run <name> <secret> <input>: one isolated run with private, empty TMPDIR and HOME
run() {
  local name="$1" secret="$2" input="$3"
  mkdir -p "$WORK/$name.tmp" "$WORK/$name.home"
  TMPDIR="$WORK/$name.tmp" HOME="$WORK/$name.home" RAKSHA_SECRET="$secret" \
    "$PY" main.py --input "$input" --output "$WORK/$name" >"$WORK/$name.stdout" 2>"$WORK/$name.stderr"
  echo $?
}

echo "1) Unit tests"
if "$TEST_PY" -m pytest -q -p no:cacheprovider >"$WORK/pytest.log" 2>&1; then
  pass "$(tail -1 "$WORK/pytest.log")"
else
  fail "pytest: $(tail -1 "$WORK/pytest.log")"
fi
find tests -name __pycache__ -type d -prune -exec rm -rf {} +

echo "2) Practice data: three runs"
code=$(run a "$SECRET_A" "$KIT/dev/input")
[ "$code" = "0" ] && pass "exit code 0" || fail "exit code $code"
for d in a.tmp a.home; do
  [ -z "$(ls -A "$WORK/$d")" ] && pass "${d#a.} left empty" || fail "${d#a.} has leftover files"
done
run b "$SECRET_A" "$KIT/dev/input" >/dev/null
if diff -rq "$WORK/a/sanitized" "$WORK/b/sanitized" >/dev/null && cmp -s "$WORK/a/records.jsonl" "$WORK/b/records.jsonl"; then
  pass "same secret twice gives identical output"
else
  fail "same secret twice gave different output"
fi
run c "$SECRET_B" "$KIT/dev/input" >/dev/null
if diff -rq "$WORK/a/sanitized" "$WORK/c/sanitized" >/dev/null && cmp -s "$WORK/a/records.jsonl" "$WORK/c/records.jsonl"; then
  warn "a different secret gave identical output (expected until fake IDs and date shifts exist)"
else
  pass "a different secret gives different output"
fi
leaked=""
for name in a b c; do
  secret="$SECRET_A"; [ "$name" = "c" ] && secret="$SECRET_B"
  if grep -rqF -- "$secret" "$WORK/$name" "$WORK/$name.stdout" "$WORK/$name.stderr" "$WORK/$name.tmp" "$WORK/$name.home" 2>/dev/null; then
    leaked="$leaked $name"
  fi
done
[ -z "$leaked" ] && pass "secret never written to output, logs, temp or home" || fail "secret found in run(s):$leaked"
if "$PY" "$KIT/validate_output.py" --input "$KIT/dev/input" --output "$WORK/a" | grep -q "CONTRACT OK"; then
  pass "CONTRACT OK"
else
  fail "output contract problems"
fi

echo "3) Scrambled data (seed $SEED)"
CHAOS="$WORK/chaos"
if "$PY" tools/chaos_mutator.py --kit "$KIT" --out "$CHAOS" --seed "$SEED" >"$WORK/chaos.log" 2>&1; then
  pass "$(tail -1 "$WORK/chaos.log")"
  # Calibration: every inserted value must be visible in an untouched copy of the scrambled input.
  "$PY" tools/leak_check.py --inserted "$CHAOS/inserted.json" --output "$CHAOS/input" >"$WORK/calibration.txt"
  if grep -q "(100.0% leaked)" "$WORK/calibration.txt"; then
    pass "leak check calibrated (all inserted values visible in the unchanged scrambled input)"
  else
    fail "leak check calibration: $(head -1 "$WORK/calibration.txt")"
  fi
  code=$(run chaos_out "$SECRET_A" "$CHAOS/input")
  [ "$code" = "0" ] && pass "exit code 0 on scrambled data" || fail "exit code $code on scrambled data"
  if "$PY" "$KIT/validate_output.py" --input "$CHAOS/input" --output "$WORK/chaos_out" | grep -q "CONTRACT OK"; then
    pass "CONTRACT OK on scrambled data"
  else
    fail "output contract problems on scrambled data"
  fi
else
  fail "scrambler failed"
fi

echo "4) Quality on practice data (organizers' check_dev.py)"
"$PY" "$KIT/check_dev.py" --output "$WORK/a" >"$WORK/dev_practice.txt" 2>&1
for pattern in "PERSONAL DATA" "by language" "by hiding place" "CANARIES" "MEDICAL DATA" "PATIENT LINKING" "RAW COPIES"; do
  echo "  $(grep -m1 "$pattern" "$WORK/dev_practice.txt" | sed 's/^ *//')"
done
echo "5) Leaks on scrambled data (our leak check)"
if [ -d "$WORK/chaos_out" ]; then
  "$PY" tools/leak_check.py --inserted "$CHAOS/inserted.json" --output "$WORK/chaos_out" | sed 's/^/  /'
fi

echo "6) Attacks on practice data (our attacker, tools/attacker_lite.py)"
if "$PY" tools/attacker_lite.py --kit "$KIT" --output "$WORK/a" --other "$WORK/c" >"$WORK/attack.txt" 2>&1; then
  sed 's/^/  /' "$WORK/attack.txt"
else
  fail "attacker failed"
fi

if [ "$SUBMIT" = "1" ]; then
  echo "7) Submission check"
  "$PY" "$KIT/check_submission.py" --repo "$REPO" --run | grep -E "❌|⚠️|READY|FIX" | sed 's/^/  /'
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "VERIFY OK"
else
  echo "VERIFY FAILED ($FAILS)"
fi
[ "$FAILS" -eq 0 ]
