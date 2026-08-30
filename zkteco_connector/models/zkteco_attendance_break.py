# -*- coding: utf-8 -*-
import logging

import pytz

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class ZktecoAttendanceBreak(models.Model):
    """Pause réelle d'un employé, dérivée des pointages (mode punch state ON).

    Une pause = le trou entre un pointage « Pause sortie » (status 2) et le
    « Pause retour » (status 3) suivant. Sert au CONTRÔLE RH : qui est en pause,
    durée réelle vs allouée (pause de l'horaire), dépassement. Pas de paie ici —
    la pause reste un trou non payé côté hr.attendance.
    """
    _name = 'zkteco.attendance.break'
    _description = 'ZKTeco — Pause employé'
    _order = 'break_start desc'

    employee_id = fields.Many2one(
        'hr.employee', string='Employé', required=True,
        ondelete='cascade', index=True)
    attendance_id = fields.Many2one(
        'hr.attendance', string='Présence', ondelete='cascade', index=True,
        help="Présence à laquelle la pause manuelle est rattachée.")
    device_id = fields.Many2one(
        'zkteco.device', string='Pointeuse', readonly=True,
        help="Pointeuse où la pause a été démarrée.")
    source = fields.Selection(
        [('device', 'Pointeuse'), ('manual', 'Manuel')],
        default='device', index=True, string='Source',
        help="Manuel = pause saisie par le RH (non pointée). Protégée du moteur "
             "de résolution et déduite des heures travaillées.")

    break_start = fields.Datetime(string='Début pause', required=True, index=True)
    break_end   = fields.Datetime(string='Fin pause',
                                  help="Vide = pause en cours.")

    date = fields.Date(string='Jour', compute='_compute_date', store=True, index=True)
    duration_real = fields.Float(
        string='Durée réelle', compute='_compute_duration', store=True,
        help="Durée réelle de la pause (heures). Vide tant qu'elle est en cours.")
    duration_allowed = fields.Float(
        string='Durée allouée', readonly=True,
        help="Pause prévue par l'horaire (heures).")
    tolerance = fields.Float(
        string='Tolérance', readonly=True,
        help="Marge tolérée autour de l'allouée (heures).")
    deviation = fields.Float(
        string='Écart', compute='_compute_duration', store=True,
        help="Durée réelle − allouée. Positif = dépassement, négatif = écourtée.")
    state = fields.Selection([
        ('ongoing', 'En cours'),
        ('done',    'Terminée'),
    ], string='Statut', compute='_compute_duration', store=True, index=True)
    compliance = fields.Selection([
        ('ongoing', 'En cours'),
        ('ok',      'Conforme'),
        ('over',    'Dépassement'),
        ('under',   'Écourtée'),
    ], string='Conformité', compute='_compute_duration', store=True, index=True,
       help="Conforme si l'écart reste dans la tolérance de l'horaire.")

    _uniq_emp_start = models.Constraint(
        'UNIQUE(employee_id, break_start)',
        'Une seule pause par employé et heure de début.',
    )

    @staticmethod
    def _emp_tz(employee):
        name = (employee.tz
                or (employee.resource_calendar_id and employee.resource_calendar_id.tz)
                or 'UTC')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    @api.depends('break_start', 'employee_id')
    def _compute_date(self):
        for b in self:
            if b.break_start:
                tz = self._emp_tz(b.employee_id)
                b.date = pytz.utc.localize(b.break_start).astimezone(tz).date()
            else:
                b.date = False

    @api.depends('break_start', 'break_end', 'duration_allowed', 'tolerance')
    def _compute_duration(self):
        for b in self:
            if b.break_start and b.break_end:
                b.duration_real = (b.break_end - b.break_start).total_seconds() / 3600.0
                b.deviation = b.duration_real - b.duration_allowed
                b.state = 'done'
                if b.deviation > b.tolerance:
                    b.compliance = 'over'
                elif b.deviation < -b.tolerance:
                    b.compliance = 'under'
                else:
                    b.compliance = 'ok'
            else:
                b.duration_real = 0.0
                b.deviation = 0.0
                b.state = 'ongoing'
                b.compliance = 'ongoing'

    @api.model_create_multi
    def create(self, vals_list):
        """Pause saisie depuis une présence (one2many) → marquée manuelle, employé
        et durée allouée hérités de la présence/de l'horaire."""
        for vals in vals_list:
            att_id = vals.get('attendance_id')
            if not att_id:
                continue
            att = self.env['hr.attendance'].browse(att_id)
            vals.setdefault('source', 'manual')
            if not vals.get('employee_id'):
                vals['employee_id'] = att.employee_id.id
            if 'duration_allowed' not in vals:
                vals['duration_allowed'] = self._allowed_for_attendance(att)
        return super().create(vals_list)

    @api.model
    def _allowed_for_attendance(self, attendance):
        """Pause prévue par l'horaire de l'employé le jour de la présence (heures)."""
        cal = attendance.employee_id.resource_calendar_id
        if not cal or not attendance.check_in:
            return 0.0
        tz = self._emp_tz(attendance.employee_id)
        dow = str(pytz.utc.localize(attendance.check_in).astimezone(tz).weekday())
        lines = cal.attendance_ids.filtered(
            lambda l: l.day_period == 'lunch' and l.dayofweek == dow)
        return sum(l.hour_to - l.hour_from for l in lines)