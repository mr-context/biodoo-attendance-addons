
from . import models
from . import wizard


def _post_init_hook(env):
    """Corrige les employes sans version et la vue hr_employee_public"""
    # 1. Corriger la vue pour accepter LEFT JOIN
    env['hr.employee']._fix_employee_public_view()
    # 2. Creer des versions pour les employes qui n'en ont pas
    env['hr.employee']._fix_employees_without_version()
    # 3. Mettre le state par defaut pour les versions existantes
    env.cr.execute("""
        UPDATE hr_version
        SET state = 'draft'
        WHERE state IS NULL OR state = ''
    """)
