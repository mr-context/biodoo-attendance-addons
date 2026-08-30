{
    'name': 'Algérie - Prestations Travail',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Payroll',
    'summary': 'Génération automatique des prestations depuis les présences',
    'description': """
Module de gestion des prestations de travail pour l'Algérie
===========================================================

Ce module fait le pont entre:
- hr.attendance (pointages BioTime)
- hr.work.entry (prestations pour la paie)

Fonctionnalités:
- Conversion automatique présences → prestations
- Gestion des shifts cross-day (chevauchant minuit)
- Détection des anomalies (absences, retards, heures sup)
- Interface de validation RH
- Types de prestations algériens

Flux:
1. BioTime → hr.attendance (données brutes)
2. hr.attendance → hr.work.entry (calcul automatique)
3. Comparaison théorique vs réel → anomalies
4. Validation RH
5. hr.work.entry → hr.payslip (paie)
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'hr_work_entry',
        'hr_attendance',
        'hr_holidays',
    ],
    'data': [
        'security/hr_dz_work_entry_security.xml',
        'security/ir.model.access.csv',
        'data/hr_work_entry_type_data.xml',
        'wizard/hr_work_entry_compute_wizard_views.xml',
        'views/hr_work_entry_views.xml',
        'views/hr_work_entry_anomaly_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
