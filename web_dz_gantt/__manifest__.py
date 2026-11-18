{
    'name': 'Vue Gantt (biodoo)',
    'summary': "Vue Gantt : enregistrements sur une ligne de temps, par ligne (employé, projet…)",
    'description': """
Vue Gantt générique
===================

Type de vue ``gantt`` : une ligne par groupe (employé, type…), le temps en
colonnes (jour, semaine, mois, année), une barre par enregistrement entre sa
date de début et sa date de fin.

Écrite pour biodoo ; la description de vue reprend les attributs publics de la
vue Gantt d'Odoo (``date_start``, ``date_stop``, ``color``, ``default_group_by``,
``scales``, ``default_scale``/``default_range``, ``total_row``,
``display_unavailability``, ``progress_bar``…) pour qu'une vue écrite pour
l'une s'affiche avec l'autre.

Côté serveur, les modules métier redéfinissent ``_gantt_unavailability``
(périodes grisées) et ``_gantt_progress_bar`` (barre de progression par ligne).

Incompatible avec ``web_gantt`` (Odoo Enterprise) : les deux déclarent le type
de vue ``gantt``.
""",
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'author': 'MESSAOUDI ABDERRAOUF',
    'license': 'LGPL-3',
    'depends': ['web'],
    'excludes': ['web_gantt'],
    'assets': {
        'web.assets_backend_lazy': [
            'web_dz_gantt/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'web_dz_gantt/static/tests/**/*',
        ],
    },
    'installable': True,
    'auto_install': False,
}
