from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    late_sanction_enabled = fields.Boolean(
        related='company_id.late_sanction_enabled',
        readonly=False,
    )
    late_sanction_mode = fields.Selection(
        related='company_id.late_sanction_mode',
        readonly=False,
    )
