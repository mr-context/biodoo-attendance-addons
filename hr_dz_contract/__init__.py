
from . import models
from . import wizard


def _post_init_hook(env):
    """Crée une version aux employés qui n'en ont pas.

    (La vue SQL hr_employee_public d'Odoo n'est plus modifiée : tout employé
    a une version, elle n'a donc pas besoin d'un LEFT JOIN.)"""
    # 1. Creer des versions pour les employes qui n'en ont pas
    env['hr.employee']._fix_employees_without_version()
    # 3. Mettre le state par defaut pour les versions existantes
    env.cr.execute("""
        UPDATE hr_version
        SET state = 'draft'
        WHERE state IS NULL OR state = ''
    """)
