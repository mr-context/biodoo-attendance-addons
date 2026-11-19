# -*- coding: utf-8 -*-
from odoo import fields, models, _

OFFICER = 'hr_attendance.group_hr_attendance_officer'


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Bouton « Assiduité » : mois en cours, recalculé à l'ouverture de la fiche
    # (même lecture que l'analyse complète, voir assiduity_analysis.py).
    dz_assiduity_score = fields.Integer(
        string="Score d'assiduité", compute='_compute_dz_assiduity', groups=OFFICER)
    dz_assiduity_level = fields.Selection([
        ('none', 'Pas de donnée'),
        ('good', 'Bon'),
        ('warning', 'À surveiller'),
        ('danger', 'Insuffisant'),
    ], string="Niveau d'assiduité", compute='_compute_dz_assiduity', groups=OFFICER)
    dz_assiduity_late_count = fields.Integer(
        string='Retards du mois', compute='_compute_dz_assiduity', groups=OFFICER)

    def _compute_dz_assiduity(self):
        analysis = self.env['hr.dz.assiduity.analysis']
        for employee in self:
            if not employee.id:
                employee.dz_assiduity_score = 0
                employee.dz_assiduity_level = 'none'
                employee.dz_assiduity_late_count = 0
                continue
            summary = analysis.period_summary(employee)
            employee.dz_assiduity_score = summary['score'] or 0
            employee.dz_assiduity_level = summary['level']
            employee.dz_assiduity_late_count = summary['late_count']

    def action_open_assiduity_analysis(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'biodoo_employee_assiduity',
            'name': _('Assiduité — %s', self.name),
            'params': {'employee_id': self.id},
            'context': {'active_id': self.id},
        }

    def action_print_assiduity(self, date_from=None, date_to=None):
        self.ensure_one()
        return self.env.ref('hr_dz_attendance_dashboard.action_report_employee_assiduity').report_action(
            self, data={'employee_ids': self.ids, 'date_from': date_from, 'date_to': date_to})
