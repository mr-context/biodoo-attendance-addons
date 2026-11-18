from . import models
from . import wizard


def _dz_setup(env):
    """Politique de présence par défaut pour chaque société ; règles d'heures
    supplémentaires algériennes à la place des règles par défaut d'Odoo (taux 1),
    pour que les heures supplémentaires restent majorées."""
    env['res.company'].search([])._dz_ensure_attendance_policy()
    env['hr.version']._dz_adopt_dz_overtime_ruleset()


def post_init_hook(env):
    _dz_setup(env)
