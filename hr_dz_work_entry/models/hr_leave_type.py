from odoo import fields, models


class HrLeaveType(models.Model):
    _inherit = 'hr.leave.type'

    work_entry_type_id = fields.Many2one(
        'hr.work.entry.type',
        string='Type de prestation',
        help="Type de prestation (hr.work.entry) généré lors du calcul des prestations "
             "pour les employés en congé de ce type.",
    )
