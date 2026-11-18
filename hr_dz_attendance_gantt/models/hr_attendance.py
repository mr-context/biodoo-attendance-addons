from odoo import models


class HrAttendance(models.Model):
    """Présences en Gantt : planning et lignes des employés (mixin RH)."""
    _name = 'hr.attendance'
    _inherit = ['hr.attendance', 'hr.dz.gantt.employee.mixin']
