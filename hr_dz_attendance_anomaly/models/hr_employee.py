# -*- coding: utf-8 -*-
from datetime import date

from odoo import models, fields, _


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    attendance_month_count = fields.Integer(
        string='Présences ce mois', compute='_compute_attendance_month_count')

    def _compute_attendance_month_count(self):
        month_start = date.today().replace(day=1)
        for employee in self:
            employee.attendance_month_count = self.env['hr.attendance'].search_count([
                ('employee_id', '=', employee.id),
                ('check_in', '>=', month_start),
            ])

    def action_open_attendance_calendar(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Calendrier de présence — %s') % self.name,
            'res_model': 'hr.attendance',
            'view_mode': 'calendar,list,form',
            'domain': [('employee_id', '=', self.id)],
        }
