# -*- coding: utf-8 -*-
"""Tableau des absences MULTI-EMPLOYÉS sur une période (à la demande, sans cron).

Pour chaque employé ayant un calendrier, on balaye les jours ouvrés et on classe :
  - présent          : un pointage existe ce jour
  - absent           : jour ouvré, pas férié, pas de congé, AUCUN pointage (injustifié)
  - congé (justifié) : couvert par un congé validé (hr.leave) ou une absence ressource
  - férié            : couvert par un jour férié global du calendrier

Sortie : une ligne de synthèse par employé (compteurs) + le détail des jours absents.
"""

import pytz
import logging
from datetime import date, datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DAYS_FR = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']


class AttendanceAbsenceDay(models.TransientModel):
    _name = 'attendance.absence.day'
    _description = "Jour d'absence (détail)"
    _order = 'date asc'

    summary_id = fields.Many2one(
        'attendance.absence.summary', ondelete='cascade', required=True)
    employee_id = fields.Many2one(related='summary_id.employee_id', store=True)
    date = fields.Date(string='Date')
    dayofweek_label = fields.Char(string='Jour')
    status = fields.Selection([
        ('absent', 'Absent (injustifié)'),
        ('leave', 'Congé (justifié)'),
    ], string='Statut')
    leave_name = fields.Char(string='Motif')


class AttendanceAbsenceSummary(models.TransientModel):
    _name = 'attendance.absence.summary'
    _description = "Synthèse absences par employé"
    _order = 'absent_days desc, employee_id'

    wizard_id = fields.Many2one(
        'attendance.absence.report', ondelete='cascade', required=True)
    employee_id = fields.Many2one('hr.employee', string='Employé', required=True)
    department_id = fields.Many2one(
        related='employee_id.department_id', store=True, string='Département')

    work_days = fields.Integer(string='Jours ouvrés')
    present_days = fields.Integer(string='Présences')
    absent_days = fields.Integer(string='Absences', help='Injustifiées')
    leave_days = fields.Integer(string='Congés', help='Absences justifiées')
    late_count = fields.Integer(string='Retards')
    late_minutes = fields.Integer(string='Retard (min)')
    early_count = fields.Integer(string='Départs anticipés')
    absence_rate = fields.Float(string="Taux d'absence (%)", digits=(5, 1))

    day_ids = fields.One2many(
        'attendance.absence.day', 'summary_id', string='Jours absents')


class AttendanceAbsenceReport(models.TransientModel):
    _name = 'attendance.absence.report'
    _description = 'Tableau des absences (multi-employés)'

    date_from = fields.Date(
        string='Du', required=True,
        default=lambda self: date.today().replace(day=1))
    date_to = fields.Date(
        string='Au', required=True, default=lambda self: date.today())

    department_id = fields.Many2one('hr.department', string='Département')
    employee_ids = fields.Many2many(
        'hr.employee', string='Employés',
        help="Vide = tous les employés actifs (filtrés par département si renseigné).")

    state = fields.Selection([
        ('draft', 'Sélection'),
        ('done', 'Résultats'),
    ], default='draft')

    summary_ids = fields.One2many(
        'attendance.absence.summary', 'wizard_id', string='Synthèse')

    # ── Totaux ────────────────────────────────────────────────────────────
    total_employees = fields.Integer(string='Employés analysés', compute='_compute_totals')
    total_absent_days = fields.Integer(string='Total absences', compute='_compute_totals')
    total_leave_days = fields.Integer(string='Total congés', compute='_compute_totals')
    employees_with_absence = fields.Integer(
        string='Employés absents', compute='_compute_totals')

    @api.depends('summary_ids', 'summary_ids.absent_days', 'summary_ids.leave_days')
    def _compute_totals(self):
        for wiz in self:
            wiz.total_employees = len(wiz.summary_ids)
            wiz.total_absent_days = sum(wiz.summary_ids.mapped('absent_days'))
            wiz.total_leave_days = sum(wiz.summary_ids.mapped('leave_days'))
            wiz.employees_with_absence = len(
                wiz.summary_ids.filtered(lambda s: s.absent_days > 0))

    # ── Analyse ───────────────────────────────────────────────────────────
    def action_analyze(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("La date de début doit être avant la date de fin."))

        self.summary_ids.unlink()

        # Population d'employés
        employees = self.employee_ids
        if not employees:
            domain = [('active', '=', True), ('resource_calendar_id', '!=', False)]
            if self.department_id:
                domain.append(('department_id', '=', self.department_id.id))
            employees = self.env['hr.employee'].search(domain)
        elif self.department_id:
            employees = employees.filtered(
                lambda e: e.department_id == self.department_id)

        has_leave = 'hr.leave' in self.env

        summaries = []
        for employee in employees:
            calendar = employee.resource_id.calendar_id if employee.resource_id else False
            if not calendar:
                continue
            tz = pytz.timezone(calendar.tz or 'Africa/Algiers')

            period_start_utc = self._to_utc(tz, self.date_from, 0, 0, 0)
            period_end_utc = self._to_utc(tz, self.date_to, 23, 59, 59)

            # Pointages de la période → dates présentes + retards/départs
            attendances = self.env['hr.attendance'].search([
                ('employee_id', '=', employee.id),
                ('check_in', '>=', period_start_utc),
                ('check_in', '<=', period_end_utc),
            ])
            present_dates = set()
            late_count = late_minutes = early_count = 0
            for att in attendances:
                d = fields.Datetime.context_timestamp(
                    att.with_context(tz=tz.zone), att.check_in).date()
                present_dates.add(d)
                if att.late_minutes:
                    late_count += 1
                    late_minutes += att.late_minutes
                if att.early_leave_minutes:
                    early_count += 1

            # Jours fériés globaux du calendrier
            global_leaves = self.env['resource.calendar.leaves'].search([
                ('date_from', '<=', fields.Datetime.to_string(period_end_utc)),
                ('date_to', '>=', fields.Datetime.to_string(period_start_utc)),
                ('resource_id', '=', False),
                '|', ('calendar_id', '=', calendar.id), ('calendar_id', '=', False),
            ])

            # Congés validés de l'employé (hr.leave) — absences justifiées
            leaves = self.env['hr.leave'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                ('date_from', '<=', fields.Datetime.to_string(period_end_utc)),
                ('date_to', '>=', fields.Datetime.to_string(period_start_utc)),
            ]) if has_leave else self.env['hr.attendance']  # empty fallback

            work_days = present = absent = leave_days = 0
            day_vals = []
            current = self.date_from
            while current <= self.date_to:
                dow = str(current.weekday())
                if not calendar.attendance_ids.filtered(lambda l, d=dow: l.dayofweek == d):
                    current += timedelta(days=1)
                    continue  # jour non travaillé

                day_start = self._to_utc(tz, current, 0, 0, 0)
                day_end = self._to_utc(tz, current, 23, 59, 59)

                # Férié → ni ouvré ni absent
                if self._covered(global_leaves, day_start, day_end):
                    current += timedelta(days=1)
                    continue

                work_days += 1
                if current in present_dates:
                    present += 1
                else:
                    leave_hit = self._covered_leave(leaves, day_start, day_end)
                    if leave_hit:
                        leave_days += 1
                        day_vals.append({
                            'date': current,
                            'dayofweek_label': DAYS_FR[current.weekday()],
                            'status': 'leave',
                            'leave_name': leave_hit,
                        })
                    else:
                        absent += 1
                        day_vals.append({
                            'date': current,
                            'dayofweek_label': DAYS_FR[current.weekday()],
                            'status': 'absent',
                        })
                current += timedelta(days=1)

            if not work_days:
                continue

            rate = round(100.0 * absent / work_days, 1) if work_days else 0.0
            summaries.append({
                'wizard_id': self.id,
                'employee_id': employee.id,
                'work_days': work_days,
                'present_days': present,
                'absent_days': absent,
                'leave_days': leave_days,
                'late_count': late_count,
                'late_minutes': late_minutes,
                'early_count': early_count,
                'absence_rate': rate,
                'day_ids': [(0, 0, dv) for dv in day_vals],
            })

        if not summaries:
            raise UserError(_(
                "Aucun employé avec calendrier de travail trouvé sur la période."))

        self.env['attendance.absence.summary'].create(summaries)
        self.write({'state': 'done'})
        return self._reopen()

    # ── Helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _to_utc(tz, d, h, m, s):
        return (tz.localize(datetime(d.year, d.month, d.day, h, m, s))
                .astimezone(pytz.UTC).replace(tzinfo=None))

    @staticmethod
    def _covered(leaves, day_start, day_end):
        for lv in leaves:
            lf = fields.Datetime.from_string(lv.date_from)
            lt = fields.Datetime.from_string(lv.date_to)
            if lf <= day_end and lt >= day_start:
                return True
        return False

    @staticmethod
    def _covered_leave(leaves, day_start, day_end):
        for lv in leaves:
            lf = fields.Datetime.from_string(lv.date_from)
            lt = fields.Datetime.from_string(lv.date_to)
            if lf <= day_end and lt >= day_start:
                name = lv.holiday_status_id.name if 'holiday_status_id' in lv._fields else None
                return name or _('Congé')
        return False

    def action_back(self):
        self.write({'state': 'draft'})
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'attendance.absence.report',
            'res_id': self.id,
            'views': [[False, 'form']],
            'target': 'new',
        }