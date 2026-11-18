"""Injecte des pointages de pointeuse dans une base de TEST, sans bridge ni NATS.

Rejoue la chaîne réelle côté Odoo : chaque pointage passe par
`zkteco.ta.handler.handle_nats_event` (le même code que les messages NATS du
bridge) → sas des pointages bruts → présences → anomalies → prestations.

À lancer avec le shell d'Odoo, depuis odooVx/odoo19 :

    BIODOO_SCENARIO=biodoo_attendance/outils/scenarios/semaine_demo.json \\
    .venv/bin/python odoo-bin shell -c odoo.conf \\
        --addons-path=addons,biodoo_attendance,biodoo_payroll_dz \\
        -d test_payroll_dz --no-http < biodoo_attendance/outils/injecter_pointages.py

Scénario (JSON) :
    {"serial": "DEMO0001",
     "pointages": [{"pin": "1", "heure": "2026-10-04T08:02:00+01:00", "statut": 0}, …]}
ou, pour une semaine type :
    {"serial": "DEMO0001", "semaine": "2026-10-04",
     "employes": [{"pin": "1", "horaire": [["08:00", "12:00"], ["13:00", "17:00"]]},
                  {"pin": "9", "horaire": [["22:00", "06:00"]]}],
     "jours": [0, 1, 2, 3, 4]}

Garde-fou : refuse toute base dont le nom ne contient ni « test » ni « demo ».
"""
import json
import os
from datetime import date, datetime, timedelta

if not any(word in env.cr.dbname for word in ('test', 'demo')):   # noqa: F821 (shell Odoo)
    raise SystemExit("Base « %s » refusée : l'injecteur ne tourne que sur une base de test ou de démo."
                     % env.cr.dbname)                              # noqa: F821

scenario = json.loads(open(os.environ['BIODOO_SCENARIO'], encoding='utf-8').read())
serial = scenario.get('serial', 'DEMO0001')
handler = env['zkteco.ta.handler']                                 # noqa: F821
TZ = scenario.get('fuseau', '+01:00')


def punches():
    for p in scenario.get('pointages', []):
        yield p['pin'], p['heure'], p.get('statut', 0)
    if 'semaine' in scenario:
        start = date.fromisoformat(scenario['semaine'])
        for offset in scenario.get('jours', [0, 1, 2, 3, 4]):
            day = start + timedelta(days=offset)
            for emp in scenario['employes']:
                for begin, end in emp['horaire']:
                    b = datetime.combine(day, datetime.strptime(begin, '%H:%M').time())
                    e = datetime.combine(day, datetime.strptime(end, '%H:%M').time())
                    if e <= b:                       # poste de nuit : sortie le lendemain
                        e += timedelta(days=1)
                    yield emp['pin'], b.isoformat() + TZ, 0
                    yield emp['pin'], e.isoformat() + TZ, 1


count = 0
for pin, when, status in punches():
    handler.handle_nats_event('zkteco.ta.attendance.%s' % serial, {
        'SerialNumber': serial, 'UserID': str(pin), 'Timestamp': when,
        'Status': status, 'VerifyMode': '1'})
    count += 1
env.cr.commit()                                                    # noqa: F821
print('Injecté : %d pointage(s) pour la pointeuse %s.' % (count, serial))
