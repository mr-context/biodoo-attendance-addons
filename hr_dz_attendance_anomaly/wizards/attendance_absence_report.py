# -*- coding: utf-8 -*-
"""Tableau des absences MULTI-EMPLOYÉS sur une période (à la demande, sans cron).

Les journées sont classées par le moteur de prestations (day_facts) : même
planning, mêmes fériés, mêmes congés, même délai de constat d'absence que la
paie. Le rapport ne peut donc pas annoncer une absence que la paie ignore, ni
l'inverse.
  - présent          : pointé (y compris entrée sans sortie)
  - absent           : jour ouvré sans pointage, délai de constat écoulé
  - congé (justifié) : congé validé
  - férié, jour non travaillé, hors contrat, pas encore constatable : non comptés

Sortie : une ligne de synthèse par employé (compteurs) + le détail des jours absents.
"""

import logging
from datetime import date

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
        default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(
        string='Au', required=True, default=lambda self: fields.Date.context_today(self))

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

        engine = self.env['hr.dz.work.entry.engine']
        summaries = []
        for employee in employees:
            work_days = present = absent = leave_days = 0
            late_count = late_minutes = early_count = 0
            day_vals = []
            for fact in engine.day_facts(employee, self.date_from, self.date_to):
                for att in fact['attendances']:
                    if att.late_minutes:
                        late_count += 1
                        late_minutes += att.late_minutes
                    if att.early_leave_minutes:
                        early_count += 1
                status, day = fact['status'], fact['day']
                if status in ('present', 'missing_punch'):
                    if fact['planned'] > 0:
                        work_days += 1
                        present += 1
                elif status == 'leave':
                    work_days += 1
                    leave_days += 1
                    day_vals.append({'date': day, 'dayofweek_label': DAYS_FR[day.weekday()],
                                     'status': 'leave',
                                     'leave_name': fact['leave'].holiday_status_id.name or _('Congé')})
                elif status == 'absent':
                    work_days += 1
                    absent += 1
                    day_vals.append({'date': day, 'dayofweek_label': DAYS_FR[day.weekday()],
                                     'status': 'absent'})

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