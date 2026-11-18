#!/usr/bin/env python3
"""Contrôles automatiques du code biodoo (règles de CONTRIBUTING.md).

Échoue (code 1) si une règle est enfreinte hors des exceptions déclarées :

  R1  `except Exception` / `except:` qui avale l'erreur : le bloc doit
      relancer (`raise`), ou journaliser (`_logger.`) ET le dire à
      l'utilisateur ou au RH (message, notification, champ d'erreur).
      Un `# biodoo: except-ok <raison>` sur la ligne `except` le justifie.
  R2  `date.today()`, `datetime.now()`, `datetime.today()` : interdits hors
      de l'utilitaire de fuseau. Utiliser `fields.Date.context_today`,
      `fields.Datetime.now()` (UTC) ou l'utilitaire `hr_dz_base.tools.dztime`.
      `# biodoo: time-ok <raison>` le justifie.
  R3  `str(e)` / `str(exc)` renvoyé à l'utilisateur dans un contrôleur.
  R4  (retirée) Les codes de toutes les règles de la structure valent 0
      avant le calcul (hr.payslip._get_baselocaldict, l10n_dz_hr_payroll) :
      lire TRANSPORT quand l'option est coupée donne 0, prouvé par le test
      test_options_off_compute.
  R5  `except` dans le code Python d'une règle salariale (XML) : une erreur
      de règle doit arrêter le calcul, jamais changer le montant en silence.

  R6  Terme interdit dans un libellé visible (menus, actions, vues) : le
      glossaire (docs/glossaire.md) fixe un mot par notion ; les restes
      d'anglais (« Devices », « Users device »…) et le double sens
      d'« anomalie » pour les retenues sont refusés.

Usage : python3 outils/verifier_code.py [--liste]
"""
import ast
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]          # odooVx/odoo19
ROOTS = [BASE / 'biodoo_attendance', BASE / 'biodoo_payroll_dz']
EXCLUDED = {'payroll', 'web_enterprise', 'web_map', 'ai', 'ai_fields', 'bridge',
            'contrat_recurence', '__pycache__', 'outils', 'node_modules'}
TIME_UTIL = {'dztime.py'}


def python_files():
    for root in ROOTS:
        for path in root.rglob('*.py'):
            parts = set(path.relative_to(root).parts)
            if parts & EXCLUDED or 'tests' in parts:
                continue
            yield path


def _handler_text(lines, handler):
    end = getattr(handler, 'end_lineno', handler.lineno)
    return '\n'.join(lines[handler.lineno - 1:end])


def check_r1(path, tree, lines):
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in ('Exception', 'BaseException'))
        if not broad:
            continue
        if 'biodoo: except-ok' in lines[node.lineno - 1]:
            continue
        body = _handler_text(lines, node)
        reraises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
        logs = '_logger.' in body or 'logger.' in body
        tells = re.search(r"message_post|notification|_notify|UserError|ValidationError|"
                          r"\b\w*error\w*\s*=|\berror\b|last_error|'error'|\"error\"", body)
        if reraises or (logs and tells):
            continue
        issues.append((node.lineno, 'R1 exception avalée'))
    return issues


def check_r2(path, lines):
    if path.name in TIME_UTIL:
        return []
    issues = []
    for i, line in enumerate(lines, 1):
        code = line.split('#', 1)[0]
        if re.search(r'\b(date\.today|datetime\.now|datetime\.today)\(\)', code) \
                and 'biodoo: time-ok' not in line:
            issues.append((i, 'R2 date/heure sans fuseau'))
    return issues


def check_r3(path, lines):
    if 'controllers' not in path.parts:
        return []
    return [(i, 'R3 erreur technique renvoyée à l\'utilisateur')
            for i, line in enumerate(lines, 1)
            if re.search(r"\bstr\((e|exc|err|error)\)", line) and '_logger' not in line]


def check_r4():
    issues = []
    for root in ROOTS:
        for path in list(root.rglob('*salary_rule*.xml')) + list(root.rglob('*prime_salary_rules*.xml')) + list(root.rglob('*loan_data*.xml')):
            text = path.read_text(encoding='utf-8')
            for m in re.finditer(r'<field name="amount_python_compute">(.*?)</field>', text, re.S):
                code = m.group(1)
                if re.search(r'\bexcept\b', code):
                    issues.append((path, text[:m.start()].count('\n') + 1, 'R5 except dans une règle salariale'))
    return issues


# (motif, raison) — cherchés dans les attributs name="…"/string="…" et les
# <field name="name"> des fichiers XML de vues et de menus.
FORBIDDEN_TERMS = [
    (r'\bDevices?\b', 'dire « pointeuse »'),
    (r'\bUsers? device\b', 'dire « utilisateurs des pointeuses »'),
    (r'\bà mapper\b', 'dire « à rattacher »'),
    (r'Anomalies à traiter', 'les retenues sont des « retenues », pas des anomalies'),
    (r'Déductions à valider', 'dire « retenues à décider »'),
    (r'\b(Never|Just now|Subscriptions|Disconnected)\b', 'texte anglais'),
]


def check_r6():
    issues = []
    for root in ROOTS:
        for path in list(root.rglob('views/*.xml')) + list(root.rglob('static/src/**/*.xml')):
            if any(part in EXCLUDED for part in path.parts):
                continue
            for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                labels = re.findall(r'(?:\bname|\bstring|\btitle)="([^"]*)"', line) + \
                    re.findall(r'<field name="name">([^<]*)</field>', line) + \
                    re.findall(r'>([^<>{}]*[A-Za-zÀ-ÿ][^<>{}]*)<', line)
                for label in labels:
                    for pattern, why in FORBIDDEN_TERMS:
                        if re.search(pattern, label):
                            issues.append((path, i, 'R6 terme interdit « %s » : %s' % (label.strip(), why)))
    return issues


def main():
    issues = []
    for path in python_files():
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            issues.append((path, exc.lineno or 0, 'syntaxe : %s' % exc.msg))
            continue
        for line, msg in check_r1(path, tree, lines) + check_r2(path, lines) + check_r3(path, lines):
            issues.append((path, line, msg))
    issues += check_r4()
    issues += check_r6()
    for path, line, msg in sorted(issues, key=lambda i: (str(i[0]), i[1])):
        print('%s:%s: %s' % (path.relative_to(BASE), line, msg))
    counts = {}
    for _p, _l, msg in issues:
        counts[msg.split()[0]] = counts.get(msg.split()[0], 0) + 1
    print('\nTotal : %d (%s)' % (len(issues), ', '.join('%s=%d' % kv for kv in sorted(counts.items())) or 'aucune'))
    return 1 if issues else 0


if __name__ == '__main__':
    sys.exit(main())
