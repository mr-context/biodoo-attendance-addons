# -*- coding: utf-8 -*-
"""Rapport d'anomalies de présence par période pour UN employé.
Révèle absences, retards et départs anticipés par rapport au planning officiel.

Les journées viennent du moteur de prestations (day_facts) : même planning,
mêmes fériés et même constat d'absence que la paie."""

import logging
from datetime import date

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DAYS_FR = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']


class AttendanceAnomalyReportLine(models.TransientModel):
    _name = 'attendance.anomaly.report.line'
    _description = 'Ligne rapport anomalie présence'
    _order = 'date asc'

    wizard_id = fields.Many2one(
        'attendance.anomaly.report', ondelete='cascade', required=True)

    date = fields.Date(string='Date')
    dayofweek_label = fields.Char(string='Jour')
    scheduled_hours = fields.Float(string='H. prévues', digits=(4, 2))

    check_in = fields.Datetime(string='Entrée')
    check_out = fields.Datetime(string='Sortie')
    worked_hours = fields.Float(string='H. travaillées', digits=(4, 2))

    status = fields.Selection([
        ('present', 'Présent'),
        ('anomaly', 'Anomalie'),
        ('absent', 'Absent'),
        ('holiday', 'Jour férié'),
    ], string='Statut', default='absent')
    holiday_name = fields.Char(string='Nom du jour férié')

    late_minutes = fields.Integer(string='Retard (min)')
    early_leave_minutes = fields.Integer(string='Départ ant. (min)')

    anomaly_ids = fields.Many2many(
        'hr.attendance.anomaly.type',
        'anomaly_report_line_anomaly_rel',
        'line_id', 'anomaly_type_id',
        string='Anomalies',
    )
    attendance_id = fields.Many2one('hr.attendance', string='Pointage')


class AttendanceAnomalyReport(models.TransientModel):
    _name = 'attendance.anomaly.report'
    _description = "Rapport d'anomalies de présence"

    employee_id = fields.Many2one(
        'hr.employee', string='Employé', required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    date_from = fields.Date(
        string='Du', required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_to = fields.Date(
        string='Au', required=True,
        default=lambda self: fields.Date.context_today(self),
    )

    state = fields.Selection([
        ('draft', 'Sélection'),
        ('done', 'Résultats'),
    ], default='draft')

    line_ids = fields.One2many(
        'attendance.anomaly.report.line', 'wizard_id', string='Détail')

    # ── Statistiques calculées ────────────────────────────────────────────
    total_work_days = fields.Integer(
        string='Jours ouvrables', compute='_compute_stats', help='Jours fériés exclus')
    total_present = fields.Integer(string='Présences', compute='_compute_stats')
    total_absent = fields.Integer(string='Absences', compute='_compute_stats')
    total_holidays = fields.Integer(string='Jours fériés', compute='_compute_stats')
    total_anomalies = fields.Integer(string='Avec anomalie', compute='_compute_stats')
    total_late = fields.Integer(string='Retards', compute='_compute_stats')
    total_late_minutes = fields.Integer(string='Total retard (min)', compute='_compute_stats')
    total_early_leave = fields.Integer(string='Départs anticipés', compute='_compute_stats')
    total_worked_hours = fields.Float(string='H. travaillées', digits=(4, 2), compute='_compute_stats')
    total_scheduled_hours = fields.Float(string='H. prévues', digits=(4, 2), compute='_compute_stats')
    delta_hours = fields.Float(
        string='Écart (h)', digits=(4, 2), compute='_compute_stats',
        help='H. travaillées − H. prévues (négatif = déficit)')

    @api.depends('line_ids', 'line_ids.status', 'line_ids.late_minutes',
                 'line_ids.early_leave_minutes', 'line_ids.worked_hours',
                 'line_ids.scheduled_hours')
    def _compute_stats(self):
        for wizard in self:
            lines = wizard.line_ids
            work_lines = lines.filtered(lambda l: l.status != 'holiday')
            present_lines = lines.filtered(lambda l: l.status in ('present', 'anomaly'))
            wizard.total_work_days = len(work_lines)
            wizard.total_present = len(present_lines)
            wizard.total_absent = len(lines.filtered(lambda l: l.status == 'absent'))
            wizard.total_holidays = len(lines.filtered(lambda l: l.status == 'holiday'))
            wizard.total_anomalies = len(lines.filtered(lambda l: l.status == 'anomaly'))
            wizard.total_late = len(lines.filtered(lambda l: l.late_minutes > 0))
            wizard.total_late_minutes = sum(lines.mapped('late_minutes'))
            wizard.total_early_leave = len(lines.filtered(lambda l: l.early_leave_minutes > 0))
            wizard.total_worked_hours = sum(lines.mapped('worked_hours'))
            wizard.total_scheduled_hours = sum(work_lines.mapped('scheduled_hours'))
            wizard.delta_hours = wizard.total_worked_hours - wizard.total_scheduled_hours

    # ── Actions ──────────────────────────────────────────────────────────
    def action_analyze(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("La date de début doit être avant la date de fin."))

        employee = self.employee_id
        if not employee.resource_id or not employee.resource_id.calendar_id:
            raise UserError(
                _("L'employé '%s' n'a pas de calendrier de travail défini.") % employee.name)

        self.line_ids.unlink()

        lines_vals = []
        for fact in self.env['hr.dz.work.entry.engine'].day_facts(
                employee, self.date_from, self.date_to):
            status, day = fact['status'], fact['day']
            if status in ('off', 'pending') and not fact['attendances']:
                continue
            line_vals = {
                'wizard_id': self.id,
                'date': day,
                'dayofweek_label': DAYS_FR[day.weekday()],
                'scheduled_hours': fact['planned'],
            }
            attendances = fact['attendances']
            if status == 'holiday' and not attendances:
                line_vals.update({'status': 'holiday', 'holiday_name': fact['holiday'].name})
            elif status == 'leave':
                line_vals.update({'status': 'holiday',
                                  'holiday_name': fact['leave'].holiday_status_id.name})
            elif attendances:
                first, last = attendances[0], attendances[-1]
                line_vals.update({
                    'status': 'anomaly' if any(attendances.mapped('is_anomaly')) else 'present',
                    'check_in': first.check_in,
                    'check_out': last.check_out,
                    'worked_hours': sum(attendances.mapped('worked_hours')),
                    'late_minutes': first.late_minutes,
                    'early_leave_minutes': last.early_leave_minutes,
                    'anomaly_ids': [(6, 0, attendances.anomaly_ids.ids)],
                    'attendance_id': first.id,
                })
            else:
                line_vals['status'] = 'absent'
            lines_vals.append(line_vals)

        if not lines_vals:
            raise UserError(_(
                "Aucun jour ouvrable trouvé dans la période sélectionnée.\n"
                "Vérifiez le calendrier de travail de l'employé."))

        self.env['attendance.anomaly.report.line'].create(lines_vals)
        self.write({'state': 'done'})
        return self._reopen()

    def action_back(self):
        self.write({'state': 'draft'})
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'attendance.anomaly.report',
            'res_id': self.id,
            'views': [[False, 'form']],
            'target': 'new',
        }

    def action_open_attendances(self):
        self.ensure_one()
        attendance_ids = self.line_ids.filtered(
            lambda l: l.attendance_id).mapped('attendance_id').ids
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pointages — %s') % self.employee_id.name,
            'res_model': 'hr.attendance',
            'view_mode': 'list,form',
            'domain': [('id', 'in', attendance_ids)],
            'target': 'current',
        }