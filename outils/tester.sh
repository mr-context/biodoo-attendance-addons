#!/usr/bin/env bash
# Lance TOUS les contrôles biodoo et affiche un résumé.
#
#   outils/tester.sh            contrôles de code + tests Odoo + tests Go
#   outils/tester.sh --hoot     + tests JS (Hoot) dans un navigateur (Playwright)
#
# Base de test : $BIODOO_TEST_DB (défaut test_payroll_dz). Ports 8199/8198,
# jamais ceux de production. Ne lance JAMAIS nats-bridge : les tests Go sont
# des tests unitaires (go test), pas le service.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ODOO="$(cd "$HERE/../.." && pwd)"                     # odooVx/odoo19
PROJECTS="$(cd "$ODOO/../.." && pwd)"                 # Documents/projects
DB="${BIODOO_TEST_DB:-test_payroll_dz}"
RESULTS=()
FAILED=0

step() { RESULTS+=("$1 : $2"); [ "$2" = "OK" ] || FAILED=1; }

echo "== 1. Contrôles de code (outils/verifier_code.py)"
if python3 "$HERE/verifier_code.py" > /tmp/biodoo_verif.txt; then step "Contrôles de code" OK
else tail -1 /tmp/biodoo_verif.txt; step "Contrôles de code" "ÉCHEC ($(tail -1 /tmp/biodoo_verif.txt))"; fi

echo "== 2. Tests Odoo (Python)"
MODULES=$(cd "$ODOO" && ls -d biodoo_attendance/*/ biodoo_payroll_dz/*/ | while read d; do
  m=$(basename "$d"); [ -f "$d/__manifest__.py" ] || continue
  case "$m" in payroll|web_enterprise|web_map|ai|ai_fields) continue;; esac
  [ -d "$d/tests" ] && printf "/%s," "$m"; done)
LOG=/tmp/biodoo_odoo_tests.log
( cd "$ODOO" && .venv/bin/python odoo-bin -c odoo.conf \
    --addons-path=addons,biodoo_attendance,biodoo_payroll_dz -d "$DB" --db-filter="^$DB\$" \
    --http-port 8199 --gevent-port 8198 --max-cron-threads=0 \
    --test-tags "${MODULES%,}" --stop-after-init ) > "$LOG" 2>&1
SUMMARY=$(grep -E "of [0-9]+ tests" "$LOG" | tail -1 | sed 's/.*INFO [^:]*: //')
if grep -qE "0 failed, 0 error\(s\)" <<< "$SUMMARY"; then step "Tests Odoo" "OK"
else grep -E "^(FAIL|ERROR):|ERROR.*(FAIL|ERROR):" "$LOG" | head -20; step "Tests Odoo" "ÉCHEC ($SUMMARY)"; fi
echo "   $SUMMARY"

echo "== 3. Tests Go"
for proj in zkteco_push_connector/zkteco-push zkteco_push_connector/zkteco-adms biodoo_supervisor biodoo_cli; do
  dir="$PROJECTS/$proj"; [ -d "$dir" ] || continue
  if (cd "$dir" && go vet ./... >/dev/null 2>&1 && go test ./... > /tmp/biodoo_go.log 2>&1); then step "Go $proj" OK
  else tail -5 /tmp/biodoo_go.log; step "Go $proj" ÉCHEC; fi
done

if [ "${1:-}" = "--hoot" ]; then
  echo "== 4. Tests JS (Hoot)"
  if python3 "$HERE/tester_hoot.py" "$DB"; then step "Tests Hoot" OK; else step "Tests Hoot" ÉCHEC; fi
fi

echo
echo "================ RÉSUMÉ ================"
printf '%s\n' "${RESULTS[@]}"
exit $FAILED
