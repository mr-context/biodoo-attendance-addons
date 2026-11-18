{
    'name': 'Présences en Gantt (biodoo)',
    'summary': "Pointages en vue Gantt : une ligne par employé, ses entrées-sorties sur la ligne de temps",
    'description': """
Les présences (hr.attendance) en vue Gantt :

- une ligne par employé, heures hors planning grisées ;
- une barre par pointage, de l'entrée à la sortie ; en rouge un pointage
  anormal (sortie manquante d'un jour passé, plus de 16 h) ;
- jour (heures), semaine ou mois ; total des heures travaillées ;
- lecture seule par défaut (les pointages viennent des pointeuses) : une
  correction passe par le formulaire, tracé ; le glisser-déposer peut être
  activé en retirant ``disable_drag_drop`` de la vue.
""",
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'author': 'MESSAOUDI ABDERRAOUF',
    'license': 'LGPL-3',
    'depends': ['hr_dz_gantt', 'hr_attendance'],
    'data': [
        'views/hr_attendance_views.xml',
    ],
    'assets': {
        'web.assets_backend_lazy': [
            'hr_dz_attendance_gantt/static/src/**/*',
        ],
    },
    'installable': True,
    'auto_install': True,
}
