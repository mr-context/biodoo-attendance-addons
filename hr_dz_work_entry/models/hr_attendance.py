"""
Extension de hr.attendance pour le lien avec hr.work.entry.
"""

from odoo import models, fields


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    work_entry_ids = fields.Many2many(
        'hr.work.entry',
        'hr_attendance_work_entry_rel',
        'attendance_id',
        'work_entry_id',
        string='Prestations liées',
        readonly=True,
    )
