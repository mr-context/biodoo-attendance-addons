from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    attendance_policy_id = fields.Many2one(
        related='company_id.attendance_policy_id', readonly=False,
        string='Politique de présence par défaut')
