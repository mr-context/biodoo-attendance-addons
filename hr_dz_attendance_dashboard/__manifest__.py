{
    'name': 'HR DZ — Tableau de bord Présences',
    'version': '19.0.1.1.0',
    'category': 'Human Resources/Attendances',
    'summary': "Bandeau statistique dynamique (présents/absents/retards) au-dessus de la vue Présences",
    'description': """
Tableau de bord Présences
==========================

Ajoute un bandeau de statistiques au-dessus des vues liste/kanban de
hr.attendance, recalculé dynamiquement par date (aucun champ stocké,
aucun cron) : présents vs effectif sous contrat, absents déduits,
retards, départs anticipés, encore présents, en congé, déductions à
valider.

Même pattern technique qu'un dashboard CRM classique : surcharge OWL
du Renderer de vue via js_class + endpoint JSON-RPC de calcul.

Analyse d'assiduité individuelle : bouton « Assiduité » sur la fiche
employé (score du mois, retards) → écran d'analyse (indicateurs,
signaux d'alerte, évolution sur 12 mois, jours de la semaine, heure
d'arrivée, comparaison avec le service, calendrier, journal,
corrections manuelles) et relevé PDF signable. Lit le moteur de
prestations (day_facts) et les anomalies : rien n'est recalculé.
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'hr_attendance',
        'mail',
        'hr_dz_work_entry',
        'hr_dz_attendance_anomaly',
        'hr_dz_contract',
        'hr_holidays',
    ],
    'data': [
        'views/hr_attendance_views.xml',
        'views/hr_employee_views.xml',
        'views/res_config_settings_views.xml',
        'report/report_employee_assiduity.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'hr_dz_attendance_dashboard/static/src/js/attendance_dashboard.js',
            'hr_dz_attendance_dashboard/static/src/xml/attendance_dashboard.xml',
            'hr_dz_attendance_dashboard/static/src/scss/attendance_dashboard.scss',
            'hr_dz_attendance_dashboard/static/src/employee_assiduity/employee_assiduity.js',
            'hr_dz_attendance_dashboard/static/src/employee_assiduity/employee_assiduity.xml',
            'hr_dz_attendance_dashboard/static/src/employee_assiduity/employee_assiduity.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
