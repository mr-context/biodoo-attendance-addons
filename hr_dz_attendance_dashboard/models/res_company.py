# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Score d'assiduité (fiche employé) : 100 moins ces pénalités par événement.
    dz_score_late_penalty = fields.Float(string='Pénalité par retard', default=1.0)
    dz_score_early_penalty = fields.Float(string='Pénalité par départ anticipé', default=1.0)
    dz_score_absence_penalty = fields.Float(string='Pénalité par absence injustifiée', default=5.0)
    dz_score_missing_penalty = fields.Float(string='Pénalité par pointage incomplet', default=2.0)
    dz_score_break_penalty = fields.Float(
        string='Pénalité par pause déjeuner dépassée', default=0.0,
        help="0 par défaut : la pause dépassée est signalée sans toucher au score.")
    dz_score_good_threshold = fields.Integer(string='Score « bon » à partir de', default=90)
    dz_score_warning_threshold = fields.Integer(string='Score « à surveiller » à partir de', default=75)
    # Signaux d'alerte.
    dz_alert_late_count = fields.Integer(string='Alerte : retards sur 30 jours', default=3)
    dz_alert_break_count = fields.Integer(
        string='Alerte forte : pauses dépassées sur la période', default=3)
    dz_alert_tolerance_count = fields.Integer(
        string='Alerte : arrivées dans la tolérance', default=3)
