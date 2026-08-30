# -*- coding: utf-8 -*-
"""Rapport d'anomalies de présence par période pour UN employé.
Révèle absences, retards et départs anticipés par rapport au planning officiel."""

import pytz
import logging
from datetime import date, datetime, timedelta

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
        default=lambda self: date.today().replace(day=1),
    )
    date_to = fields.Date(
        string='Au', required=True,
        default=lambda self: date.today(),
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

        calendar = employee.resource_id.calendar_id
        tz = pytz.timezone(calendar.tz or 'Africa/Algiers')
        self.line_ids.unlink()

        period_start_utc = (tz.localize(datetime(
            self.date_from.year, self.date_from.month, self.date_from.day, 0, 0, 0))
            .astimezone(pytz.UTC).replace(tzinfo=None))
        period_end_utc = (tz.localize(datetime(
            self.date_to.year, self.date_to.month, self.date_to.day, 23, 59, 59))
            .astimezone(pytz.UTC).replace(tzinfo=None))

        public_leaves = self.env['resource.calendar.leaves'].search([
            ('date_from', '<=', fields.Datetime.to_string(period_end_utc)),
            ('date_to', '>=', fields.Datetime.to_string(period_start_utc)),
            '|', ('calendar_id', '=', calendar.id), ('calendar_id', '=', False),
            '|', ('resource_id', '=', employee.resource_id.id), ('resource_id', '=', False),
        ])

        def _is_public_holiday(day_dt_start, day_dt_end):
            for leave in public_leaves:
                leave_from = fields.Datetime.from_string(leave.date_from)
                leave_to = fields.Datetime.from_string(leave.date_to)
                if leave_from <= day_dt_end and leave_to >= day_dt_start:
                    return True, leave.name or 'Jour férié'
            return False, None

        lines_vals = []
        current = self.date_from
        while current <= self.date_to:
            dayofweek = str(current.weekday())
            day_cal_lines = calendar.attendance_ids.filtered(
                lambda l, d=dayofweek: l.dayofweek == d)
            if not day_cal_lines:
                current += timedelta(days=1)
                continue

            first_line = min(day_cal_lines, key=lambda l: l.hour_from)
            last_line = max(day_cal_lines, key=lambda l: l.hour_to)
            scheduled_hours = last_line.hour_to - first_line.hour_from

            day_start_utc = (tz.localize(datetime(
                current.year, current.month, current.day, 0, 0, 0))
                .astimezone(pytz.UTC).replace(tzinfo=None))
            day_end_utc = (tz.localize(datetime(
                current.year, current.month, current.day, 23, 59, 59))
                .astimezone(pytz.UTC).replace(tzinfo=None))

            is_holiday, holiday_name = _is_public_holiday(day_start_utc, day_end_utc)
            line_vals = {
                'wizard_id': self.id,
                'date': current,
                'dayofweek_label': DAYS_FR[current.weekday()],
                'scheduled_hours': scheduled_hours,
            }
            if is_holiday:
                line_vals.update({'status': 'holiday', 'holiday_name': holiday_name})
            else:
                attendance = self.env['hr.attendance'].search([
                    ('employee_id', '=', employee.id),
                    ('check_in', '>=', day_start_utc),
                    ('check_in', '<=', day_end_utc),
                ], order='check_in asc', limit=1)
                if attendance:
                    line_vals.update({
                        'status': 'anomaly' if attendance.is_anomaly else 'present',
                        'check_in': attendance.check_in,
                        'check_out': attendance.check_out,
                        'worked_hours': attendance.worked_hours,
                        'late_minutes': attendance.late_minutes,
                        'early_leave_minutes': attendance.early_leave_minutes,
                        'anomaly_ids': [(6, 0, attendance.anomaly_ids.ids)],
                        'attendance_id': attendance.id,
                    })
                else:
                    line_vals['status'] = 'absent'
            lines_vals.append(line_vals)
            current += timedelta(days=1)

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