# zkteco_connector — pointeuses ZKTeco

Reçoit les événements du bridge (bus NATS, via `core_nats`) et les transforme en
présences ; envoie les commandes vers les pointeuses.

## Modèles

| Modèle | Rôle |
|---|---|
| `zkteco.device` | une pointeuse (état : à approuver, approuvée, hors ligne) ; alerte persistante hors ligne > 30 min |
| `zkteco.device.user` | utilisateur de la pointeuse (PIN) et son rattachement à un employé |
| `zkteco.device.attlog` | **sas** des pointages bruts, unique (pointeuse, PIN, horodatage) ; le pointage facial du portail y passe aussi |
| `zkteco.device.biodata` / `biophoto` | empreintes biométriques |
| `zkteco.ta.handler` | traitement des sujets `zkteco.ta.*` ; `_nats_schema` = champs obligatoires par sujet |
| `zkteco.attlog.catchup.wizard` | récupérer les pointages d'une période |

## Flux d'un pointage

`zkteco.ta.attendance.<SN>` → `handle_nats_event` → `zkteco.device.attlog`
(sas) → `_resolve_one_day` sous verrou `pg_advisory_xact_lock(employé, jour)`
→ `hr.attendance` (entrée/sortie, GPS du portail).

## Droits

Groupes **Pointeuses / Consultation** et **Gestionnaire pointeuses**
(`security/zkteco_security.xml`) ; règles multi-société sur tous les modèles.

## Pièges

- La pointeuse vit en **heure locale** (`device_timezone`) ; Odoo en UTC.
- Ne jamais écrire sur une pointeuse en test (lecture seule) ; aucune commande
  destructive (CLEAR, WIPE) sans confirmation.

## Tests

`--test-tags /zkteco_connector` (résolution des journées, postes de nuit,
portail = pointeuse, alerte hors ligne, récupération par période).
