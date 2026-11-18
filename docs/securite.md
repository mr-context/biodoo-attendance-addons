# Sécurité et droits — matrice biodoo

Cette matrice est **vérifiée automatiquement** par
`biodoo_payroll_dz_suite/tests/test_securite.py` (tag `biodoo_securite`) :

1. tout modèle créé par biodoo a des droits d'accès déclarés ;
2. tout modèle qui porte une société a une règle multi-société
   (`company_id in company_ids`, ou vide = partagé) ;
3. les données sensibles ci-dessous ne sont ouvertes ni à « tout employé
   interne » (`base.group_user`) ni au portail ;
4. le visage enregistré (`hr.employee.face_embedding`) est protégé au niveau
   du champ.

Un modèle ajouté sans être classé fait échouer les tests.

## Rôles

| Rôle | Groupe Odoo | Pour qui |
|---|---|---|
| Employé (portail) | `base.group_portal` | Salarié sans accès au back-office : ses pointages, ses demandes, son solde |
| Employé interne | `base.group_user` | Utilisateur Odoo sans rôle RH |
| Consultation pointeuses | `zkteco_connector.group_zkteco_user` | Contrôle RH des pointages bruts et des photos |
| Gestionnaire pointeuses | `zkteco_connector.group_zkteco_manager` | RH qui rattache les utilisateurs des pointeuses, corrige les pointages, approuve une pointeuse — **sans être administrateur** |
| Agent RH | `hr.group_hr_user` | Dossiers salariés, contrats, demandes |
| Gestionnaire RH | `hr.group_hr_manager` | Paramétrage RH, visage enregistré, réduction de droits d'un compte |
| Paie | `payroll.group_payroll_user` | Bulletins, prêts, déclarations |
| Responsable paie | `payroll.group_payroll_manager` | Barèmes, paramètres de paie, imports |
| Administrateur | `base.group_system` | Effacement de pointeuse, enrôlement, commandes à risque, NATS |

## Données sensibles (jamais ouvertes à tous ni au portail)

| Donnée | Modèle | Lecture | Écriture |
|---|---|---|---|
| Pointages bruts (PIN, heure, pointeuse) | `zkteco.device.attlog` | Consultation pointeuses | Gestionnaire pointeuses |
| Utilisateurs de pointeuse (PIN, **carte**) | `zkteco.device.user` | Consultation pointeuses | Gestionnaire pointeuses |
| Photos de vérification | `zkteco.attphoto` | Consultation pointeuses | Administrateur |
| Empreintes, photos biométriques | `zkteco.device.biodata`, `zkteco.device.biophoto` | Administrateur | Administrateur |
| Journal des commandes pointeuse | `zkteco.device.cmd.log` | Gestionnaire pointeuses | Administrateur |
| Visage enregistré | champ `hr.employee.face_embedding` | Gestionnaire RH | Gestionnaire RH (assistant d'enrôlement) |
| Points de contrôle et leurs passages | `hr.employee.checkpoint`, `hr.checkpoint.log` | Agent RH | Gestionnaire RH |
| Prêts et échéances | `hr.loan`, `hr.loan.line` | Paie | Paie (suppression : responsable paie) |
| Déclarations DAS, ATS, 301 bis | `hr.dz.das`, `hr.dz.ats`, `hr.dz.301bis` | Paie | Responsable paie |
| Barèmes IRG | `hr.irg.bareme`, `hr.irg.table` | Agent RH | Responsable paie |

## Portail

- Le portail lit **côté serveur** les données de l'employé connecté
  (`hr.employee` dont `user_id` = l'utilisateur), jamais par un accès direct
  aux modèles sensibles.
- Tickets et demandes : règle `rule_ticket_portal` (ses propres tickets
  uniquement) ; le détail passe par `_document_check_access`.
- L'assistant d'accès portail ne gère que le compte **portail lié à
  l'employé** : le compte visé est recalculé côté serveur à chaque action
  (un compte forgé dans le formulaire est ignoré), un compte interne n'est
  jamais modifié, et réduire les droits d'un compte interne demande le rôle
  Gestionnaire RH (jamais un administrateur, jamais soi-même).

## Règles multi-société

Toutes les règles suivent la forme `[('company_id', 'in', company_ids)]`,
ou `['|', ('company_id', '=', False), ('company_id', 'in', company_ids)]`
quand un enregistrement sans société est partagé (référentiels : paramètres
légaux, modèles de contrat, barèmes). Les modèles du connecteur prennent la
société de leur pointeuse (`zkteco.device.company_id`).

## Avant d'ajouter un modèle

1. Ajouter ses lignes dans `security/ir.model.access.csv` (jamais `base.group_user`
   pour une donnée personnelle, de santé, de salaire ou biométrique).
2. S'il a un `company_id`, ajouter sa règle multi-société.
3. S'il contient une donnée sensible, l'ajouter à la liste `SENSITIVE` du test
   et à ce document.
4. Lancer `outils/tester.sh`.
