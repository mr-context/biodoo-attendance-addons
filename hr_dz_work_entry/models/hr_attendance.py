"""
Extension de hr.attendance pour le lien avec hr.work.entry.

Chaque pointage — ou heure supplémentaire approuvée, refusée, corrigée — créé,
modifié ou supprimé fait recalculer les prestations des jours touchés : une absence posée pendant qu'une pointeuse était hors ligne est
ainsi remplacée par la présence réelle dès la synchronisation.
"""

from collections import defaultdict

from odoo import api, models, fields

_SYNC_FIELDS = frozenset({'check_in', 'check_out', 'employee_id'})


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    work_entry_ids = fields.Many2many(
        'hr.work.entry',
        'hr_attendance_work_entry_rel',
        'attendance_id',
        'work_entry_id',
        string='Prestations liées',
        readonly=True,
    )

    def _dz_touched_days(self):
        engine = self.env['hr.dz.work.entry.engine']
        days = defaultdict(set)
        for att in self.sudo():
            if att.employee_id and att.check_in:
                tz = engine._tz(att.employee_id)
                days[att.employee_id].add(engine._local_date(att.check_in, tz))
        return days

    @staticmethod
    def _dz_merge(*mappings):
        merged = defaultdict(set)
        for mapping in mappings:
            for employee, days in mapping.items():
                merged[employee] |= days
        return merged

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get('dz_skip_work_entry_sync'):
            self.env['hr.dz.work.entry.engine'].sync_days(records._dz_touched_days())
        return records

    def write(self, vals):
        sync = (not self.env.context.get('dz_skip_work_entry_sync')
                and _SYNC_FIELDS.intersection(vals))
        before = self._dz_touched_days() if sync else {}
        res = super().write(vals)
        if sync:
            self.env['hr.dz.work.entry.engine'].sync_days(
                self._dz_merge(before, self._dz_touched_days()))
        return res

    def unlink(self):
        sync = not self.env.context.get('dz_skip_work_entry_sync')
        before = self.exists()._dz_touched_days() if sync else {}
        res = super().unlink()
        if sync:
            self.env['hr.dz.work.entry.engine'].sync_days(before)
        return res


class HrAttendanceOvertimeLine(models.Model):
    _inherit = 'hr.attendance.overtime.line'

    dz_compensation = fields.Selection([
        ('paid', 'Payée'),
        ('mixed', 'Majoration payée + repos'),
        ('recovered', 'Rendue en repos'),
    ], string='Compensation',
        help="Vide : selon la politique de présence de l'employé.\n"
             "Payée : heures sup. payées.\n"
             "Majoration payée + repos : seule la majoration est payée, le temps "
             "est rendu en repos.\n"
             "Rendue en repos : rien n'est payé, le repos inclut la majoration.\n"
             "Sans effet si la politique n'accorde pas de récupérations.")

    def _dz_days(self):
        days = defaultdict(set)
        for line in self.sudo():
            if line.employee_id and line.date:
                days[line.employee_id].add(line.date)
        return days

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        self.env['hr.dz.work.entry.engine'].sync_days(lines._dz_days())
        return lines

    def write(self, vals):
        res = super().write(vals)
        if {'status', 'manual_duration', 'amount_rate', 'date', 'dz_compensation'} & set(vals):
            self.env['hr.dz.work.entry.engine'].sync_days(self._dz_days())
        return res

    def unlink(self):
        days = self._dz_days()
        res = super().unlink()
        self.env['hr.dz.work.entry.engine'].sync_days(days)
        return res
