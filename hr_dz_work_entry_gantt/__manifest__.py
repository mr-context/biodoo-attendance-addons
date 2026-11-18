{
    'name': 'Prestations en Gantt (biodoo)',
    'summary': "Prestations de paie en vue Gantt : une ligne par employé, un jour par colonne",
    'description': """
Les prestations (hr.work.entry) en vue Gantt, comme le planning de paie :

- une ligne par employé, avatar et jours hors planning grisés ;
- couleur du type de prestation, durée dans la barre ;
- ligne de total des heures par jour ;
- bulle : type, durée, origine (calcul, retenue, saisie RH), état ;
- sélection de journées → remplacer par un type ou rendre au calcul
  automatique : aperçu et confirmation si des heures sup. ou retenues sont
  en jeu, jours non travaillés exclus par défaut, journal
  (Présences → Analyse → Journal des prestations modifiées) et annulation.
""",
    'version': '19.0.1.1.0',
    'category': 'Human Resources',
    'author': 'MESSAOUDI ABDERRAOUF',
    'license': 'LGPL-3',
    'depends': ['hr_dz_gantt', 'hr_dz_work_entry'],
    'data': [
        'security/ir.model.access.csv',
        'security/hr_dz_work_entry_gantt_multisociete.xml',
        'views/hr_work_entry_views.xml',
        'views/hr_dz_work_entry_change_views.xml',
    ],
    'assets': {
        'web.assets_backend_lazy': [
            'hr_dz_work_entry_gantt/static/src/**/*',
        ],
    },
    'installable': True,
    'auto_install': True,
}
