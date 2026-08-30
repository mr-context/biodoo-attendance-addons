{
    'name': 'Algeria - Base Localization',
    'version': '19.0.1.0.0',
    'category': 'Localization',
    'summary': 'Wilayas et Communes d\'Algérie',
    'description': """
Algeria Base Localization
=========================

Ce module ajoute:
- Les 69 Wilayas d'Algérie (2024) dans res.country.state
- Les communes d'Algérie dans res.city

Utilisable par les modules RH, Ventes, Achats, etc.
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'base_address_extended',
    ],
    'data': [
        'data/res_country_state_data.xml',
        'data/res_city_data.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
