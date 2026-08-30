# -*- coding: utf-8 -*-
{
    'name': 'ZKTeco Connector',
    'version': '19.0.3.0.0',
    'category': 'Human Resources/Attendances',
    'summary': 'ZKTeco ADMS via NATS — device approval, enrollment, check-in/out',
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': ['hr_attendance', 'core_nats'],
    'external_dependencies': {
        # Crop serveur du visage à l'enrôlement (wizard zkteco.enroll.face.wizard).
        # Noms d'IMPORT, pas pip : cv2 → opencv-python-headless. À installer sur
        # l'OS qui héberge Odoo (Linux ou Windows) : pip install opencv-python-headless
        'python': ['cv2', 'numpy'],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'data/zkteco_license_cron.xml',
        'views/resource_calendar_views.xml',
        'views/zkteco_device_views.xml',
        'views/zkteco_device_license_views.xml',
        'views/zkteco_device_user_views.xml',
        'views/zkteco_mapping_wizard_views.xml',
        'views/zkteco_approve_wizard_views.xml',
        'views/zkteco_enroll_wizard_views.xml',
        'views/zkteco_wipe_wizard_views.xml',
        'views/zkteco_enroll_face_wizard_views.xml',
        'views/hr_employee_views.xml',
        'views/hr_attendance_views.xml',
        'views/zkteco_attlog_views.xml',
        'views/zkteco_attendance_break_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'zkteco_connector/static/src/js/face_capture_field.js',
            'zkteco_connector/static/src/xml/face_capture_field.xml',
            'zkteco_connector/static/src/scss/fingerprint_enroll.scss',
            'zkteco_connector/static/src/scss/device_kanban.scss',
            'zkteco_connector/static/src/scss/bridge_panel.scss',
            'zkteco_connector/static/src/js/bridge_panel.js',
            'zkteco_connector/static/src/xml/bridge_panel.xml',
            'zkteco_connector/static/src/js/fingerprint_enroll_dialog.js',
            'zkteco_connector/static/src/xml/fingerprint_enroll_dialog.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}