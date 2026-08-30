# -*- coding: utf-8 -*-
{
    'name': 'Core NATS',
    'version': '19.0.2.0.0',
    'category': 'Technical',
    'summary': 'NATS JetStream infrastructure — pub/sub framework for connector modules',
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': ['base', 'web'],
    'external_dependencies': {
        'python': ['nats-py'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/nats_server_views.xml',
        'views/menus.xml',
        'views/nats_dashboard_action.xml',  # after menus.xml — needs menu_nats_root
    ],
    'assets': {
        'web.assets_backend': [
            'core_nats/static/src/css/nats_dashboard.css',
            'core_nats/static/src/xml/nats_dashboard.xml',
            'core_nats/static/src/js/nats_dashboard.js',
        ],
    },
    'post_init_hook':    'post_init_hook',
    'post_migrate_hook': 'post_migrate_hook',
    'uninstall_hook':    'uninstall_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}