#!/usr/bin/env python3
"""Lance les tests JS (Hoot) des modules biodoo dans Chromium (Playwright).

Démarre un Odoo de test sur le port 8189 (crons coupés), se connecte avec
BIODOO_TEST_LOGIN / BIODOO_TEST_PASSWORD, exécute les suites `@<module>` des
modules biodoo qui ont un dossier static/tests, puis arrête le serveur.

    BIODOO_TEST_LOGIN=… BIODOO_TEST_PASSWORD=… python3 outils/tester_hoot.py [base]
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ODOO = Path(__file__).resolve().parents[2]
DB = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('BIODOO_TEST_DB', 'test_payroll_dz')
PORT = 8189


def hoot_id(descriptor):
    """Identifiant Hoot d'une suite (même calcul que web/tests/test_js.py)."""
    value = 0
    for char in descriptor:
        value = ((value << 5) - value + ord(char)) & 0xFFFFFFFF
    return f'{value:08x}'


def suites():
    for root in ('biodoo_attendance', 'biodoo_payroll_dz'):
        for tests in sorted((ODOO / root).glob('*/static/tests')):
            yield tests.parent.parent.name


def main():
    login, password = os.environ.get('BIODOO_TEST_LOGIN'), os.environ.get('BIODOO_TEST_PASSWORD')
    if not (login and password):
        print('BIODOO_TEST_LOGIN et BIODOO_TEST_PASSWORD sont requis.')
        return 2
    from playwright.sync_api import sync_playwright
    server = subprocess.Popen(
        [str(ODOO / '.venv/bin/python'), 'odoo-bin', '-c', 'odoo.conf',
         '--addons-path=addons,biodoo_attendance,biodoo_payroll_dz', '-d', DB,
         f'--db-filter=^{DB}$', f'--http-port={PORT}', '--gevent-port=8188',
         '--max-cron-threads=0'], cwd=ODOO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = True
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            for _ in range(90):
                try:
                    page.goto(f'http://localhost:{PORT}/web/login?db={DB}')
                    break
                except Exception:          # serveur pas encore prêt
                    time.sleep(1)
            page.fill('input[name=login]', login)
            page.fill('input[name=password]', password)
            page.click('button[type=submit]')
            page.wait_for_url('**/odoo**', timeout=60000)
            for module in suites():
                logs = []
                page.on('console', lambda m, logs=logs: logs.append(m.text))
                page.goto(f'http://localhost:{PORT}/web/tests?headless&loglevel=2&preset=desktop'
                          f'&timeout=15000&id={hoot_id("@" + module)}')
                for _ in range(300):
                    if any('[HOOT] Test suite' in line for line in logs):
                        break
                    time.sleep(1)
                passed = any('Test suite succeeded' in line for line in logs)
                print(f'{module} : {"OK" if passed else "ÉCHEC"}')
                if not passed:
                    ok = False
                    print('\n'.join(line for line in logs if 'failed' in line.lower())[-2000:])
    finally:
        server.terminate()
        server.wait(timeout=30)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
