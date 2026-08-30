# -*- coding: utf-8 -*-
{
    'name': 'HR DZ — Anomalies & Absences de présence',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Attendances',
    'summary': "Détection d'anomalies de pointage (retard, départ anticipé...), "
               "déductions paie et tableau des absences — connecteur-agnostique",
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    # Extrait de biotime_connector, débranché de BioTime. Tolérances sur res.company,
    # déductions → work entries LATE/EARLY (via hr_dz_work_entry / hr_work_entry).
    'depends': [
        'hr_attendance',
        'hr_work_entry',
        'hr_dz_work_entry',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/attendance_anomaly_types.xml',
        'views/hr_attendance_anomaly_type_views.xml',
        'views/hr_attendance_deduction_views.xml',
        'views/hr_attendance_views.xml',
        'views/hr_attendance_calendar_views.xml',
        'reports/report_attendance_sheet.xml',
        'views/attendance_anomaly_report_views.xml',
        'views/attendance_absence_report_views.xml',
        'views/res_company_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}