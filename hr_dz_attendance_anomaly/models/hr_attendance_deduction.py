# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class HrAttendanceDeduction(models.Model):
    _name        = 'hr.attendance.deduction'
    _description = 'Déduction présence (retard / départ anticipé)'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Employé',
        required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id')

    attendance_id = fields.Many2one(
        'hr.attendance', string='Présence',
        ondelete='cascade', index=True)
    date = fields.Date(string='Date', required=True, index=True)

    deduction_type = fields.Selection([
        ('late',  'Retard'),
        ('early', 'Départ anticipé'),
    ], string='Type', required=True)

    duration = fields.Float(string='Durée (h)', required=True)

    status = fields.Selection([
        ('to_validate', 'À valider'),
        ('validated',   'Validée'),
        ('refused',     'Refusée'),
    ], string='État', default='to_validate', required=True, store=True)

    validated_by   = fields.Many2one('res.users', string='Validé par',   readonly=True, copy=False)
    validated_date = fields.Datetime(string='Date validation',            readonly=True, copy=False)

    # Prestation générée (work entry LATE / EARLY)
    work_entry_id = fields.Many2one(
        'hr.work.entry', string='Prestation',
        readonly=True, copy=False, ondelete='set null')

    is_manager = fields.Boolean(compute='_compute_is_manager')

    @api.depends('employee_id')
    def _compute_is_manager(self):
        has_mgr = self.env.user.has_group('hr_attendance.group_hr_attendance_manager')
        has_off = self.env.user.has_group('hr_attendance.group_hr_attendance_officer')
        for ded in self:
            ded.is_manager = has_mgr or (
                has_off and ded.employee_id.attendance_manager_id == self.env.user
            )

    def write(self, vals):
        if 'status' in vals:
            attendances = self.mapped('attendance_id').filtered('id')
            if attendances:
                self.env.add_to_compute(
                    attendances._fields['deduction_status'], attendances)
        return super().write(vals)

    def _compute_sanction_duration(self):
        """Durée de déduction selon le mode de sanction de la société.

        - minutes  : durée réelle du retard (self.duration)
        - half_day : moitié de la journée théorique
        - full_day : journée complète théorique
        Appliqué uniquement aux retards ; les départs anticipés gardent la durée exacte.
        """
        self.ensure_one()
        company = self.employee_id.company_id
        if (self.deduction_type != 'late'
                or not company.late_sanction_enabled
                or company.late_sanction_mode == 'minutes'):
            return self.duration

        calendar = self.employee_id.resource_calendar_id
        hours_per_day = calendar.hours_per_day if calendar else 8.0
        if company.late_sanction_mode == 'half_day':
            return hours_per_day / 2.0
        if company.late_sanction_mode == 'full_day':
            return hours_per_day
        return self.duration

    def action_validate(self):
        """Valide les déductions et crée les work entries LATE / EARLY."""
        for ded in self.filtered(lambda d: d.status == 'to_validate'):
            if 'hr.work.entry' in self.env:
                code = 'LATE' if ded.deduction_type == 'late' else 'EARLY'
                wtype = self.env['hr.work.entry.type'].search([('code', '=', code)], limit=1)
                if wtype:
                    try:
                        duration = ded._compute_sanction_duration()
                        if ded.work_entry_id:
                            ded.work_entry_id.write({'duration': duration})
                        else:
                            ded.work_entry_id = self.env['hr.work.entry'].create({
                                'employee_id':        ded.employee_id.id,
                                'date':               ded.date,
                                'duration':           duration,
                                'work_entry_type_id': wtype.id,
                            })
                    except Exception as e:
                        _logger.warning(
                            "Work entry déduction impossible pour %s : %s", ded.id, e)
            ded.write({
                'status':         'validated',
                'validated_by':   self.env.user.id,
                'validated_date': fields.Datetime.now(),
            })

    def action_refuse(self):
        self.filtered(lambda d: d.status != 'refused').write({'status': 'refused'})