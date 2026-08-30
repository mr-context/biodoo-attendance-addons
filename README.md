# biodoo-attendance-addons

![License: LGPL-3.0](https://img.shields.io/badge/License-LGPL_v3-blue.svg)

Modules Odoo **biodoo — Présence / Pointage** : pointage ZKTeco (bridge NATS),
pointage facial, prestations de travail dérivées des présences, détection
d'anomalies, et socle RH/contrats — **sans aucune dépendance vers la paie**.

Variante « présence seule » de la suite biodoo. Sous licence **LGPL-3**.
Chaque module suit son propre versioning (champ `version` du manifeste).

## Installation

```bash
# ajouter ce dossier à l'addons_path d'Odoo, puis :
odoo -d <base> -i biodoo_attendance_suite --stop-after-init
```

Le méta-module `biodoo_attendance_suite` installe l'ensemble via ses dépendances.

## Modules

| Module | Version | Description |
|---|---|---|
| `biodoo_attendance_suite` | 19.0.1.0.0 | Méta-module : installe le produit Présence/Pointage ZKTeco (sans paie) |
| `core_nats` | 19.0.2.0.0 | Infrastructure NATS JetStream — framework pub/sub pour le connecteur |
| `zkteco_connector` | 19.0.3.0.0 | ZKTeco ADMS via NATS — approbation device, enrôlement, check-in/out |
| `hr_face_attendance` | 19.0.1.0.0 | Pointage facial depuis le portail employé (InsightFace + MiniFASNet) |
| `hr_dz_work_entry` | 19.0.1.0.0 | Génération automatique des prestations depuis les présences |
| `hr_dz_attendance_anomaly` | 19.0.1.0.0 | Détection d'anomalies de pointage (retard, départ anticipé…) |
| `hr_dz_attendance_dashboard` | 19.0.1.0.0 | Bandeau statistique dynamique (présents/absents/retards) |
| `hr_dz_base` | 19.0.1.0.0 | Module RH de base pour entreprises algériennes |
| `hr_dz_contract` | 19.0.1.0.0 | Gestion des contrats de travail algériens |
| `l10n_dz_base` | 19.0.1.0.0 | Wilayas et Communes d'Algérie |
| `l10n_dz_company` | 19.0.1.0.0 | Identifiants légaux algériens sur la fiche société (NIF, NIS, RC, CNAS…) |
| `web_enterprise` | 1.0 | Surcouche client web Odoo |

## Le bridge ZKTeco

Le connecteur communique avec les pointeuses ZKTeco via un **bridge NATS**
distribué séparément (binaire licencié, non inclus dans ce repo). Voir
l'installeur biodoo pour le déploiement.
