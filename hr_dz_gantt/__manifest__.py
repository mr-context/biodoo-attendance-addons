{
    'name': 'Gantt RH (biodoo)',
    'summary': "Vue Gantt RH : avatar de l'employé et jours hors planning grisés",
    'description': """
Couche RH de la vue Gantt biodoo :

- vue ``hr_gantt`` (js_class) : avatar de l'employé en tête de ligne ;
- ``hr.dz.gantt.employee.mixin`` à hériter par tout modèle qui porte un
  ``employee_id`` : heures hors planning grisées (planning du contrat, comme
  le calcul des prestations, plus fériés et congés) et une ligne pour chaque
  employé sous contrat, même sans donnée sur la période.
""",
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'author': 'MESSAOUDI ABDERRAOUF',
    'license': 'LGPL-3',
    # hr_dz_work_entry : seule définition du « prévu » (planning du contrat).
    'depends': ['hr', 'web_dz_gantt', 'hr_dz_work_entry'],
    'assets': {
        'web.assets_backend_lazy': [
            'hr_dz_gantt/static/src/**/*',
        ],
    },
    'installable': True,
    'auto_install': True,
}
