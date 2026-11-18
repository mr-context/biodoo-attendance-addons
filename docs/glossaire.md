# Glossaire biodoo — un mot par notion

Ce glossaire fixe le vocabulaire de l'interface, de la documentation et des
messages. Le vérificateur (`outils/verifier_code.py`, règle R6) refuse les
termes interdits dans les vues et les menus.

| Notion | Mot à employer | À ne pas employer |
|---|---|---|
| Le terminal biométrique ZKTeco | **pointeuse** | device, terminal, machine |
| Une personne enregistrée dans une pointeuse | **utilisateur de la pointeuse** (son n° : PIN) | user device, sas |
| Relier cet utilisateur à un employé | **rattacher** | mapper |
| Un passage enregistré par la pointeuse | **pointage brut** | attlog, log |
| Un pointage brut qui n'a pas pu être apparié | **pointage brut rejeté** | anomalie de pointage |
| La présence reconstituée (entrée → sortie) | **présence** | attendance |
| Un écart de présence (retard, sortie manquante, durée anormale…) | **anomalie** — et seulement cela | — |
| La somme retirée du salaire pour un retard / départ anticipé | **retenue** (« retenues à décider ») | anomalie, déduction |
| Ce qui doit être corrigé par les RH | **À corriger** (file unique) | à traiter |
| Une journée valorisée pour la paie | **prestation** | work entry |
| Le droit d'utiliser une pointeuse avec biodoo | **emplacement de licence** | slot |
| Le gabarit biométrique (doigt, visage, paume) | **empreinte** (biométrique) | template, biodata |
| Une demande d'un salarié (congé, sortie, mission, prêt) | **demande RH** | ticket |
| Le relais entre pointeuses et Odoo | **bridge** (service « nats-bridge ») | — |
| Le canal de messages entre le bridge et Odoo | **bus des pointeuses (NATS)** | — |
| Un message que le bus n'a pas pu traiter | **message en échec** | dead letter |
| Le produit | **biodoo** (le nom commercial TimeMaster désigne l'offre) | — |

## Paie

| Notion | Mot à employer |
|---|---|
| Assiette CNAS | **brut cotisable** (code GROSS) |
| Assiette IRG | **imposable** (code IMPOSABLE) |
| IRG au barème / IRG à taux fixe sur prime | **IRG** / **IRG libératoire** |
| Travailleurs handicapés et retraités (art. 104 CIDTA) | **régime IRG** « handicapé » / « retraité » |
| Indemnités exonérées de cotisations | **indemnités non cotisables** (transport, panier) |
| Fin de mois | **contrôle avant clôture**, puis **validation du lot** |
