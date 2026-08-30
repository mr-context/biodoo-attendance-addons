{
    'name': 'HR Algérie - Base',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Localization',
    'summary': 'Module RH de base pour entreprises algériennes',
    'description': """
HR Algérie - Base
=================

Module de base RH conforme à la législation algérienne (Loi 90-11).

Fonctionnalités:
- Paramètres légaux avec dates d'effet (SMIG, CNAS, IRG)
- Champs employé spécifiques Algérie (état civil, service national, etc.)
- Identifiants entreprise (NIF, NIS, RC, CNAS)
- Catégories socio-professionnelles (CSP)
- Types de placement (ANEM, CTA, etc.)
- Rapports: Attestation de travail, Certificat de travail

Conforme à:
- Loi 90-11 relative aux relations de travail
- Code des impôts (IRG)
- Réglementation CNAS
    """,
    'author': 'MESSAOUDI ABDERRAOUF',
    'website': 'https://www.smoothtechnology.net',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'l10n_dz_base',
        'l10n_dz_company',
    ],
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Data
        'data/ir_sequence_data.xml',
        'data/hr_civilite_data.xml',
        'data/hr_csp_data.xml',
        'data/hr_type_placement_data.xml',
        'data/hr_service_national_data.xml',
        'data/hr_legal_parameter_data.xml',
        # Views
        'views/hr_legal_parameter_views.xml',
        'views/hr_employee_views.xml',
        'views/hr_job_views.xml',
        'views/res_company_views.xml',
        'views/menu.xml',
        # Reports
        'reports/report_attestation_travail.xml',
        'reports/report_certificat_travail.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'hr_dz_base/static/src/css/dz_employee_style.css',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
}
