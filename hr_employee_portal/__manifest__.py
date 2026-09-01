{
    'name': 'HR Employee Portal',
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'summary': 'Portail RH employé — gestion des accès et pointage',
    'description': """
HR Employee Portal
==================

Module de portail RH permettant aux employés d'accéder à leur suivi de présence en ligne.

Fonctionnalités :
- Création de compte portail depuis la fiche employé
- Génération de PDF credentials
- Page d'accueil portail avec section RH
- Consultation du pointage mensuel en ligne
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://msd.com',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'hr_dz_base',
        'portal',
        'mail',
        'hr_attendance',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/portal_security.xml',
        'wizard/hr_portal_access_wizard_views.xml',
        'views/hr_employee_views.xml',
        'views/portal_templates.xml',
        'reports/reports.xml',
        'reports/report_portal_access.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
