# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError


class ReportEmployeeAssiduity(models.AbstractModel):
    _name = 'report.hr_dz_attendance_dashboard.report_employee_assiduity'
    _description = "Relevé d'assiduité"

    @api.model
    def _get_report_values(self, docids, data=None):
        if not self.env.user.has_group('hr_attendance.group_hr_attendance_officer'):
            raise AccessError(self.env._("Relevé d'assiduité réservé aux responsables des présences."))
        data = data or {}
        date_from = fields.Date.to_date(data.get('date_from')) if data.get('date_from') else None
        date_to = fields.Date.to_date(data.get('date_to')) if data.get('date_to') else None
        # Avec des options (période), l'URL du rapport ne porte plus les ids
        # (web/.../reports/utils.js) : ils voyagent dans data.
        ids = docids or data.get('employee_ids') or self.env.context.get('active_ids') or []
        employees = self.env['hr.employee'].browse(ids).exists()
        employees.check_access('read')
        analysis = self.env['hr.dz.assiduity.analysis']
        return {
            'doc_ids': employees.ids,
            'doc_model': 'hr.employee',
            'docs': employees,
            'analyses': {emp.id: analysis.analyse(emp, date_from, date_to) for emp in employees},
        }
