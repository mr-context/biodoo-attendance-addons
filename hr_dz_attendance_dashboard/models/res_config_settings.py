# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dz_score_late_penalty = fields.Float(related='company_id.dz_score_late_penalty', readonly=False)
    dz_score_early_penalty = fields.Float(related='company_id.dz_score_early_penalty', readonly=False)
    dz_score_absence_penalty = fields.Float(related='company_id.dz_score_absence_penalty', readonly=False)
    dz_score_missing_penalty = fields.Float(related='company_id.dz_score_missing_penalty', readonly=False)
    dz_score_break_penalty = fields.Float(related='company_id.dz_score_break_penalty', readonly=False)
    dz_alert_break_count = fields.Integer(related='company_id.dz_alert_break_count', readonly=False)
    dz_score_good_threshold = fields.Integer(related='company_id.dz_score_good_threshold', readonly=False)
    dz_score_warning_threshold = fields.Integer(
        related='company_id.dz_score_warning_threshold', readonly=False)
    dz_alert_late_count = fields.Integer(related='company_id.dz_alert_late_count', readonly=False)
    dz_alert_tolerance_count = fields.Integer(
        related='company_id.dz_alert_tolerance_count', readonly=False)
