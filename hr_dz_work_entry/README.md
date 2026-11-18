# hr_dz_work_entry — moteur de prestations

Transforme **planning + présences + congés** en **prestations** (`hr.work.entry`)
datées, celles que la paie lit.

## Modèles

| Modèle | Rôle |
|---|---|
| `hr.dz.work.entry.engine` (abstrait) | le moteur : `sync(employés, du, au)`, `day_facts(employé, du, au)` |
| `hr.attendance.policy` | tolérances de retard, sortie oubliée, compensation des heures sup. ; par société, surchargée par employé |
| `hr.dz.recovery` | récupérations (heures rendues en repos) |
| `hr.employee` | `attendance_policy_id`, `dz_work_entry_error` (dernière erreur de calcul, bandeau sur la fiche) |

## Flux

1. Une présence change → `sync_days({employé: {jours}})` (savepoint par employé).
2. Nuit : `_cron_sync_recent` recalcule du 1er du mois précédent à hier, par lots
   de 50 employés (commit entre lots).
3. Les jours déjà **validés** (dans un bulletin) ne sont jamais modifiés.
4. Toute erreur passe par `_sync_guarded` : inscrite sur l'employé, comptée au
   tableau de bord, journalisée ; les autres employés continuent.

## Points d'extension

- `_calendar(employé, version)` : quel horaire s'applique (semaines alternées, Ramadan).
- `_planned_hours(employé, calendrier, jour)` : heures prévues d'un jour.
- `_types()` : correspondance rôle → type de prestation (travail, absence, HS…).

## Pièges

- Toujours raisonner en **jour local** : `_local_date(dt_utc, tz)` (ou
  `hr_dz_base.tools.dztime`). Un pointage à 0 h 30 heure d'Alger est la veille en UTC.
- Postes de nuit : la journée de travail commence 6 h avant la prise de poste.

## Tests

`--test-tags /hr_dz_work_entry` (récupérations, postes de nuit, politiques).
