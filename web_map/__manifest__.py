# -*- coding: utf-8 -*-
{
    'name':"Map View",
    'summary':"Vue carte : affiche les enregistrements sur une carte",
    'description':"""Vue carte (map)
================
Permet d'afficher les enregistrements géolocalisés sur une carte
(OpenStreetMap par défaut, Mapbox si un jeton est configuré).

Module autonome : ne dépend que de `web` et `base_setup`, et n'active
aucune fonctionnalité sous licence propriétaire.

Basé sur le module `web_map` d'Odoo S.A., distribué sous LGPL-3.""",
    'category': 'Hidden',
    'version':'1.0',
    'depends':['web', 'base_setup'],
    'data':[
        "views/res_config_settings.xml",
        "views/res_partner_views.xml",
    ],
    'auto_install': True,
    'author': 'SmoothTechnology',
    'website': 'https://smoothtechnology.work',
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend_lazy': [
            'web_map/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'web_map/static/lib/**/*',
            'web_map/static/tests/**/*',
        ],
        'web.qunit_suite_tests': [
            'web_map/static/lib/**/*',
        ],
    }
}
