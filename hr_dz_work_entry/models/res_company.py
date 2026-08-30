from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    late_sanction_enabled = fields.Boolean(
        string='Activer la sanction retard',
        default=False,
        help="Si activé, tout retard au-delà de la tolérance entraîne une déduction "
             "selon le mode choisi (minutes exactes, demi-journée ou journée entière).",
    )
    late_sanction_mode = fields.Selection([
        ('minutes', 'Minutes exactes de retard'),
        ('half_day', 'Demi-journée'),
        ('full_day', 'Journée entière'),
    ],
        string='Mode de déduction',
        default='minutes',
        help="Détermine la durée déduite lors d'un retard validé.\n"
             "- Minutes exactes : déduit uniquement le temps de retard réel.\n"
             "- Demi-journée : déduit la moitié de la journée de travail théorique.\n"
             "- Journée entière : déduit la journée complète.",
    )
