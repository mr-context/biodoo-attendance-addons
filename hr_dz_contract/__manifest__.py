{
    'name': 'HR Algérie - Contrats',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Localization',
    'summary': 'Gestion des contrats de travail algériens',
    'description': """
HR Algérie - Contrats
=====================

Gestion complète des contrats de travail conformément au droit algérien (Loi 90-11).

Fonctionnalités:
- Types de contrat: CDI, CDD, CTT, CTA (ANEM), Stage
- Périodes d'essai avec alertes automatiques
- Avenants (modifications de contrat)
- Renouvellement de CDD
- Confirmation/Prolongation de période d'essai
- Calcul automatique des dates de fin
- Préavis selon le poste
- Impression des contrats et avenants

Conforme à:
- Loi 90-11 relative aux relations de travail
- Code du travail algérien
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'hr_dz_base',
    ],
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Data
        'data/ir_sequence_data.xml',
        'data/hr_contract_type_data.xml',
        'data/hr_contract_article_data.xml',
        'data/hr_prime_type_data.xml',
        'data/ir_cron_data.xml',
        # Views
        'views/res_company_views.xml',
        'views/hr_contract_article_views.xml',
        'views/hr_prime_type_views.xml',
        'views/hr_version_views.xml',
        'views/hr_contract_views.xml',
        'views/hr_contract_type_views.xml',
        'views/hr_contract_avenant_views.xml',
        'views/menu.xml',
        # Wizards
        'wizard/hr_trial_confirmation_views.xml',
        'wizard/hr_contract_renewal_views.xml',
        # Reports
        'reports/report_contract.xml',
        'reports/report_avenant.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'post_init_hook': '_post_init_hook',
}
