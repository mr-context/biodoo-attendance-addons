# Guide RH — du pointage au bulletin

Ce guide suit une semaine ordinaire d'un responsable RH. Chaque écran cité est
dans le menu **Présences**, sauf mention contraire.

## 1. Chaque matin : « À corriger »

Présences → Contrôle des pointages → **À corriger**. Y arrivent seulement les
pointages qui demandent une action :

- **sortie manquante** (l'employé a oublié de pointer en partant) ;
- **entrée manquante** ;
- **retard**, **départ anticipé**, **durée anormale** ;
- **retenue à décider** (selon la politique de présence de l'employé).

Deux gestes suffisent :

- **Corriger l'heure** directement dans la liste. Pour plusieurs lignes, cochez-les
  puis modifiez l'heure d'une seule : la valeur s'applique à toutes. Un pointage
  corrigé est réexaminé ; il quitte la liste s'il n'est plus en anomalie.
- **Marquer comme vu** quand le pointage est juste (mission, réunion…).

Les retenues se décident dans **Retenues à décider** : retenir, ou ne pas retenir.

## 2. Un nouvel employé

1. Employés → Nouveau. Le **matricule** est attribué seul (unique par société).
2. Onglet contrat : type (CDI, CDD…), dates, salaire, horaire, **régime IRG**
   (cas général, travailleur handicapé, retraité).
3. Pointeuses → **À rattacher à un employé** : l'empreinte enregistrée sur la
   pointeuse apparaît ici ; choisissez l'employé.

## 3. Les demandes des salariés

Les demandes (congé, sortie, mission, prêt) suivent toujours le même circuit :
**le salarié soumet → son responsable approuve → les RH valident**. Seules les
RH valident. Un congé validé est créé dans Congés par le circuit normal d'Odoo ;
remettre la demande en brouillon refuse ce congé.

## 4. Les prestations

Chaque journée devient une **prestation** (travail, absence, congé, heures
supplémentaires…), calculée seule à partir du planning et des pointages. Si un
employé a un **bandeau rouge** « Prestations non recalculées » sur sa fiche, la
cause y est écrite (contrat manquant, horaire absent…) : corrigez-la, le calcul
de nuit réessaie.

## 5. Les alertes qui arrivent seules

Dans vos activités (cloche en haut de l'écran) :

- **Fin de période d'essai** (7 jours avant) ;
- **Fin de CDD** (30 jours avant) puis **CDD échu sans décision** ;
- **Pointeuse hors ligne** (plus de 30 minutes) pour le gestionnaire des pointeuses.

## 6. La fin de mois (gestionnaire de paie)

1. Paie → Lots → le lot du mois → **Contrôle avant clôture**.
2. Les points **bloquants** (prestations en conflit, contrat sans structure,
   bulletin non calculé) doivent être corrigés ; les points **à vérifier**
   (salarié sans bulletin, sans compte bancaire, sans NSS) méritent un regard.
3. Les totaux à déclarer s'affichent : **IRG (G50)**, **CNAS** parts salariale et
   patronale, net à virer.
4. **Préparer le virement** : fichier CSV (matricule, nom, compte, net).
5. **Valider le lot** : les bulletins ne bougent plus. Une correction de
   pointage après clôture se traite le mois suivant.

Pour comprendre un montant d'IRG : onglet **Détail IRG** du bulletin (tranches,
abattement, lissage).
