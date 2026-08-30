{
    'name': 'Algeria - Company Legal Identifiers',
    'version': '19.0.1.0.0',
    'category': 'Localization',
    'summary': 'Identifiants légaux algériens sur la fiche société (NIF, NIS, RC, CNAS…)',
    'description': """
Algeria - Company Legal Identifiers
=====================================

Ajoute les identifiants officiels algériens sur res.company :

- NIF  — Numéro d'Identification Fiscale
- NIS  — Numéro d'Identification Statistique
- RC   — Registre de Commerce
- Article d'imposition
- N° Employeur CNAS
- N° CASNOS (gérants associés)

Utilisable indépendamment des modules RH (comptabilité, ventes, achats…).
Compatible multi-société : chaque société porte ses propres identifiants.
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'l10n_dz_base',
    ],
    'data': [
        'views/res_company_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
