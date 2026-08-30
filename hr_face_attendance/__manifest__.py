{
    'name': 'HR Face Attendance',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Attendances',
    'summary': 'Pointage facial depuis le portail employé (InsightFace + MiniFASNet)',
    'description': """
HR Face Attendance
==================
Alternative au terminal biométrique physique : l'employé pointe depuis son
téléphone via le portail Odoo.

Fonctionnalités :
- Vérification d'identité 1:1 (InsightFace buffalo_s — ArcFace 512d)
- Détection de vivacité anti-spoofing (MiniFASNet V2 ONNX)
- Géofencing GPS (haversine) configurable par site
- Enrollment RH via wizard avec anneau SVG Face ID (MediaPipe head-pose)
- Portail /my/attendance dédié avec aperçu caméra live
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'hr_attendance',
        'portal',
        'bus',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/res_company_views.xml',
        'views/hr_work_location_views.xml',
        'views/hr_checkpoint_views.xml',
        'views/hr_employee_views.xml',
        'views/hr_attendance_views.xml',
        'wizard/face_enrollment_wizard_views.xml',
        'views/portal_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'hr_face_attendance/static/src/css/face_attendance.css',
            'hr_face_attendance/static/src/xml/enrollment.xml',
            'hr_face_attendance/static/src/xml/detect_location.xml',
            'hr_face_attendance/static/src/xml/geofence_map.xml',
            'hr_face_attendance/static/src/xml/checkpoint_day_map.xml',
            'hr_face_attendance/static/src/js/enrollment.js',
            'hr_face_attendance/static/src/js/detect_location.js',
            'hr_face_attendance/static/src/js/geofence_map.js',
            'hr_face_attendance/static/src/js/checkpoint_day_map.js',
        ],
        'web.assets_frontend': [
            'hr_face_attendance/static/src/css/face_attendance.css',
            'hr_face_attendance/static/src/js/face_portal.js',
        ],
    },
    'external_dependencies': {
        'python': ['insightface', 'onnxruntime', 'cv2', 'numpy'],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
