# Architecture de biodoo

Du doigt posé sur la pointeuse au virement du salaire : qui fait quoi, où, et
où regarder quand quelque chose ne va pas.

```
 Pointeuse ZKTeco ──HTTP ADMS──▶ nats-bridge (Go, poste client) ──NATS JetStream──▶ Odoo 19
   (empreinte,        :8089        • admission (licence, IP)        127.0.0.1:4222      core_nats ─▶ zkteco_connector
    visage, badge)                 • journal de secours (WAL)       jeton NATS_TOKEN    (pull, ack)   pointages bruts (sas)
                                   • commandes vers la pointeuse                                        │
                                                                                                        ▼
                                                    hr.attendance (présences) ──▶ hr_dz_attendance_anomaly
                                                             │                     (anomalies, retenues, « À corriger »)
                                                             ▼
                                                    hr_dz_work_entry : moteur de prestations
                                                             │   (planning, postes de nuit, congés, récupérations)
                                                             ▼
                                                    hr.work.entry (prestations) ──▶ file « Recalculs de paie »
                                                                                            │
                                                                                            ▼
                                                    l10n_dz_hr_payroll : bulletin (règles, IRG, CNAS)
                                                             │
                                                             ▼
                                                    Contrôle avant clôture ─▶ G50 / CNAS / virement / DAS / 301 bis
```

## 1. Pointeuse → bridge

- Les pointeuses parlent le protocole **ADMS « push »** (HTTP) au bridge
  `nats-bridge` installé chez le client (dépôt `zkteco-push`).
- **Admission** : une pointeuse n'est servie que si elle occupe un *emplacement
  de licence* (`internal/license`). La synchronisation des emplacements avec Odoo
  refuse une liste vide ou une libération massive (garde-fou).
- `X-Forwarded-For` n'est cru que d'un relais déclaré (`TRUSTED_PROXIES`).

## 2. Bridge → bus NATS

- Le bridge embarque un serveur **NATS JetStream**, n'écoutant que sur
  `127.0.0.1` (variable `NATS_HOST`), protégé par un jeton (`NATS_TOKEN`).
- Flux `TA_EVENTS` (pointages, photos, utilisateurs…) : **7 jours**, 2 Gio au plus ;
  Odoo arrêté un week-end ne perd rien.
- Si le bus est indisponible, chaque événement va dans le **journal de secours**
  (`outbox.wal`) : générations successives, jamais jetées ; au-delà de 1 Gio la plus
  ancienne est *mise de côté* (`.misdecote`), signalée, rejouable.
- Chaque message porte un identifiant déterministe : un rejeu est dédoublonné.

## 3. Bus → Odoo (`core_nats`)

- Un **consommateur JetStream durable en mode pull**, partagé par tous les
  workers Odoo : chaque message est traité **une fois**, puis acquitté.
- Échec passager → relivré plus tard (délai qui double). Après 8 essais, ou si
  le message est malformé (schéma `_nats_schema` du traitement), il est rangé dans
  **Messages en échec** (`nats.dead.letter`) et peut être relancé.
- Compteurs en base (justes avec plusieurs workers) ; tableau de bord réservé à
  l'administrateur.

## 4. Pointages bruts → présences (`zkteco_connector`)

- Chaque pointage brut entre dans le **sas** (`zkteco.device.attlog`), unique par
  (pointeuse, PIN, horodatage). Le pointage facial du portail passe par le même sas.
- La journée de travail d'un employé est résolue sous **verrou** (employé, jour) :
  deux workers ne fabriquent jamais deux présences.
- Postes de nuit : la journée commence 6 h avant la prise de poste.

## 5. Présences → anomalies → « À corriger »

- Chaque type d'anomalie est réglé par un **seuil** (mode standard) ou, pour
  l'administrateur, par une condition Python (mode expert). Une règle en erreur
  l'affiche sur sa fiche et au tableau de bord.
- Retards et départs anticipés produisent des **retenues à décider** selon la
  politique de présence de l'employé.
- La file **À corriger** rassemble tout ce qui demande une action RH.

## 6. Prestations (`hr_dz_work_entry`)

- Le moteur transforme planning + présences + congés en **prestations** datées.
  Il tourne à chaque présence terminée, et la nuit pour le mois en cours.
- Une erreur de calcul est inscrite sur la fiche de l'employé (bandeau rouge) et
  comptée au tableau de bord ; elle ne bloque pas les autres employés.

## 7. Paie (`biodoo_payroll_dz`)

- Une présence terminée met (employé, mois) dans la file **Recalculs de paie** ;
  une tâche planifiée recalcule le bulletin **brouillon**. Un bulletin à vérifier
  ou validé n'est jamais modifié.
- Règles salariales : voir `biodoo_payroll_dz/l10n_dz_hr_payroll/docs/regles_salariales.md`.
- Fin de mois : **Contrôle avant clôture** sur le lot (bloquants, totaux G50 et
  CNAS, fichier de virement), puis validation du lot.

## Où regarder quand…

| Symptôme | Où regarder |
|---|---|
| Une pointeuse n'apparaît pas | Pointeuses → Pointeuses à approuver ; emplacements de licence |
| Pointeuse « hors ligne » | Activité « Pointeuse hors ligne » sur sa fiche ; voyant du superviseur |
| Pointages qui n'arrivent pas | Paramètres → Bus des pointeuses (NATS) → tableau de bord ; Messages en échec |
| Pointage brut non transformé | Pointeuses → Pointages bruts rejetés ; À rattacher à un employé |
| Prestations fausses ou absentes | Bandeau rouge sur la fiche de l'employé ; tableau de bord « Prestations non recalculées » |
| Bulletin pas à jour | Paie → Recalculs de paie (dernière erreur) |
| Montant d'IRG contesté | Onglet « Détail IRG » du bulletin |
