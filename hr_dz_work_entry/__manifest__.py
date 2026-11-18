{
    'name': 'Algérie - Prestations Travail',
    'version': '19.0.1.2.0',
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
- Absences, retards, départs, sorties oubliées selon la politique de présence
- Interface de validation RH
- Types de prestations algériens

Flux:
1. BioTime → hr.attendance (données brutes)
2. hr.attendance → hr.work.entry (calcul automatique)
3. Comparaison prévu (planning) / réel (pointages) → prestations
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
        'mail',
    ],
    'data': [
        'security/hr_dz_work_entry_security.xml',
        'security/ir.model.access.csv',
        'data/hr_work_entry_type_data.xml',
        'data/hr_attendance_overtime_ruleset_data.xml',
        'data/hr_dz_recovery_data.xml',
        'data/ir_cron_data.xml',
        'views/hr_attendance_policy_views.xml',
        'wizard/hr_work_entry_compute_wizard_views.xml',
        'views/hr_work_entry_views.xml',
        'views/hr_dz_recovery_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
