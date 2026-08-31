# biodoo-attendance-addons

![License: LGPL-3.0](https://img.shields.io/badge/License-LGPL_v3-blue.svg)
![Odoo](https://img.shields.io/badge/Odoo-19.0-875A7B.svg)

Modules Odoo **biodoo — Présence / Pointage** : pointage ZKTeco (via bridge NATS),
pointage facial depuis le portail employé, prestations de travail dérivées des
présences, détection d'anomalies, et socle RH / contrats + localisation Algérie
(`l10n_dz`) — **sans aucune dépendance vers la paie**.

Variante « présence seule » de la suite biodoo. Sous licence **LGPL-3**.
Chaque module suit son propre versioning (champ `version` du manifeste).

---

## Installation

### Prérequis

- **Odoo 19.0** (Community ou Enterprise)
- **PostgreSQL**
- Un **bridge ZKTeco** (`nats-bridge`) pour relier les pointeuses — binaire
  distribué séparément (voir [Le bridge ZKTeco](#le-bridge-zkteco)).

### Option A — Installation automatique (Odoo + addons + bridge)

Installe Odoo 19, déploie ces addons et met en place le bridge ZKTeco en service.

**Linux (Ubuntu / Debian)** — dans un terminal :

```bash
curl -fsSL https://release.smoothtechnology.work/install.sh | sudo bash
```

**Windows** — dans PowerShell **en administrateur** :

```powershell
iex ([Text.Encoding]::UTF8.GetString((iwr https://release.smoothtechnology.work/install.ps1 -UseBasicParsing).RawContentStream.ToArray()))
```

> Le décodage UTF-8 explicite évite l'affichage en « mojibake » de la bannière et
> des accents (PowerShell 5.1 décode sinon la réponse en ISO-8859-1). Un simple
> `irm … | iex` fonctionne aussi, mais l'affichage sera moins propre.

**Reprise après interruption** : l'installeur est idempotent et garde un état de
progression (`C:\biodoo\.install_state`). En cas d'échec ou de coupure, **relancez
simplement la même commande** : les étapes déjà terminées sont ignorées et
l'installation reprend là où elle s'était arrêtée. Pour forcer une réinstallation
complète, téléchargez le script et lancez-le avec `-Fresh`.

L'installeur crée la base, patche l'`addons_path` et installe le méta-module
`biodoo_attendance_suite` (sous Linux l'installation du module est automatique ;
sous Windows elle se fait ensuite depuis **Apps → Installer**).

### Option B — Installation manuelle (addons dans un Odoo existant)

1. Cloner ce dépôt dans votre `addons_path` :

   ```bash
   git clone https://github.com/mr-context/biodoo-attendance-addons.git
   ```

2. Ajouter le dossier à l'`addons_path` dans `odoo.conf` :

   ```ini
   addons_path = /chemin/vers/odoo/addons,/chemin/vers/biodoo-attendance-addons
   ```

3. Redémarrer Odoo, puis installer le méta-module (il tire tout le reste via ses
   dépendances) :

   ```bash
   odoo -d <base> -i biodoo_attendance_suite --stop-after-init
   ```

   ou depuis l'interface : **Apps → Mettre à jour la liste des applications →
   installer « BioDoo Attendance Suite »**.

---

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

---

## Le bridge ZKTeco

Le connecteur communique avec les pointeuses ZKTeco (protocole ADMS) via un
**bridge NATS** distribué séparément — un binaire licencié qui n'est **pas inclus
dans ce dépôt**. L'installation automatique (Option A) le déploie et le configure
en service. Pour une installation manuelle, contactez
[Smooth Technology](https://www.smoothtechnology.net).

---

## Licence

Publié sous **LGPL-3.0**. Voir [`LICENSE`](LICENSE).
Auteur : MESSAOUDI ABDERRAOUF — [smoothtechnology.net](https://www.smoothtechnology.net)
