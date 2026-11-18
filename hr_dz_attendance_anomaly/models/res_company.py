# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Anciennes tolérances (avant les politiques de présence de hr_dz_work_entry).
    # Conservées pour reprendre leurs valeurs dans la politique par défaut ; les
    # tolérances en vigueur sont celles de la politique.
    late_tolerance_minutes = fields.Integer(string='Tolérance retard (min)', default=5)
    early_tolerance_minutes = fields.Integer(string='Tolérance départ anticipé (min)', default=5)
