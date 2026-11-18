# Contribuer à biodoo

## Avant d'écrire du code

1. Lire `docs/architecture.md` (le chemin d'un pointage jusqu'au bulletin) et
   `docs/glossaire.md` (un mot par notion, imposé dans l'interface).
2. Lancer l'outillage : `outils/tester.sh` (contrôles de code, tests Odoo sur la
   base `test_payroll_dz`, tests Go). `--hoot` ajoute les tests de l'interface.

## Règles vérifiées automatiquement (`outils/verifier_code.py`)

| Règle | Interdit | Comment faire |
|---|---|---|
| R1 | `except` qui avale une erreur (`pass`, simple log) | laisser remonter, ou la rendre VISIBLE (champ d'erreur, file, activité) ; un cas assumé se justifie par `# biodoo: except-ok <raison>` |
| R2 | `date.today()`, `datetime.now()` | `fields.Date.context_today(self)`, `fields.Datetime.now()` (UTC), `hr_dz_base.tools.dztime` pour jour local ↔ UTC |
| R3 | `str(e)` renvoyé à l'utilisateur par un contrôleur | message métier en français ; le détail va au journal |
| R5 | `try/except` dans une règle salariale | les codes valent 0 par défaut ; une erreur doit arrêter le calcul |
| R6 | anglais ou double sens dans un libellé (« Device », « Anomalies à traiter ») | le glossaire |

## Règles de conception

- **Rien ne se perd en silence** : une tâche planifiée traite chaque élément dans
  un savepoint et inscrit l'erreur là où un humain la verra.
- **Rien ne se devine** : un paramètre légal absent bloque la paie avec un message ;
  aucune valeur de repli écrite dans le code.
- **Droits** : tout nouveau modèle a ses ACL et, s'il a une société, sa règle
  multi-société (vérifié par `biodoo_payroll_dz_suite/tests/test_securite.py`).
  Pas de `sudo()` pour contourner un circuit natif d'Odoo (congés, validation).
- **Calcul hors de `write()`** : un `write()` met en file, une tâche calcule.
- **Odoo 19** : `models.Constraint` / `models.UniqueIndex`, `group_ids`,
  `res.groups.privilege`, `ir.rule` avec `company_ids`.
- Le code garde la langue en place (français) ; les libellés sont en français.

## Tests obligatoires

- Toute correction : un test qui échouait avant.
- Paie : un cas de référence dans `biodoo_payroll_dz/outils/cas_reference_paie.py`
  (montants calculés à la main), puis `test_cas_reference`.
- Sécurité : un test d'accès refusé pour l'utilisateur qui ne doit pas pouvoir.
- Nommer les tests en français, par ce qu'ils garantissent.

## Interdits du projet

- Ne jamais lancer `nats-bridge` sur un poste de développement (il prend les ports
  de production). Les tests Go démarrent leur propre serveur NATS embarqué.
- Pointeuses en lecture seule pendant les essais ; aucun contournement de licence.
- `_publish/`, clés et secrets hors de git (clé de signature : `~/.config/biodoo/signing/`).
