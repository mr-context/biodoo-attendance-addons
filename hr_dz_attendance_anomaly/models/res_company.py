# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Tolérances de pointage (remplacent l'ancienne biotime.config).
    # Utilisées par les règles d'anomalie 'late' / 'early_leave'.
    late_tolerance_minutes = fields.Integer(
        string='Tolérance retard (min)', default=5,
        help="En-deçà de cette durée, un retard n'est pas signalé comme anomalie.")
    early_tolerance_minutes = fields.Integer(
        string='Tolérance départ anticipé (min)', default=5,
        help="En-deçà de cette durée, un départ anticipé n'est pas signalé.")