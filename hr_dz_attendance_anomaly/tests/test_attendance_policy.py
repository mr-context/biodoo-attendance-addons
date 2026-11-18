"""
Politiques de présence : tolérances, coût des retards et départs, cumul
« N retards = X jours », mode automatique ou manuel.

Octobre 2026, horaire dimanche-jeudi 8 h-12 h / 13 h-17 h à Alger (UTC+1) ;
le 4 octobre est un dimanche. Une journée = deux segments pointés.
"""

from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_attendance_policy')
class TestAttendancePolicy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        lines = []
        for day in ('6', '0', '1', '2', '3'):
            lines += [
                (0, 0, {'name': 'Matin', 'dayofweek': day, 'hour_from': 8, 'hour_to': 12,
                        'day_period': 'morning'}),
                (0, 0, {'name': 'Après-midi', 'dayofweek': day, 'hour_from': 13, 'hour_to': 17,
                        'day_period': 'afternoon'}),
            ]
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'Dim-Jeu Alger (politique)', 'tz': 'Africa/Algiers', 'attendance_ids': lines,
        })
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Employé politique', 'resource_calendar_id': cls.calendar.id,
            'tz': 'Africa/Algiers',
        })
        cls.employee.version_id.write({
            'contract_date_start': date(2026, 1, 1),
            'resource_calendar_id': cls.calendar.id,
            'work_entry_source': 'attendance',
        })
        cls.policy = cls.env['hr.attendance.policy'].create({
            'name': 'Politique de test', 'mode': 'manual',
            'late_tolerance_minutes': 5, 'early_tolerance_minutes': 5,
        })
        cls.employee.attendance_policy_id = cls.policy
        cls.Deduction = cls.env['hr.attendance.deduction']
        cls.engine = cls.env['hr.dz.work.entry.engine']

    # ── Outils ───────────────────────────────────────────────────────────────

    def _day(self, day, late_min=0, early_min=0):
        """Une journée pointée en deux segments, heures d'Alger → UTC naïf."""
        base = datetime(day.year, day.month, day.day) - timedelta(hours=1)
        self.env['hr.attendance'].create([
            {'employee_id': self.employee.id,
             'check_in': base + timedelta(hours=8, minutes=late_min),
             'check_out': base + timedelta(hours=12)},
            {'employee_id': self.employee.id,
             'check_in': base + timedelta(hours=13),
             'check_out': base + timedelta(hours=17, minutes=-early_min)},
        ])

    def _deductions(self, dtype='late'):
        return self.Deduction.search([('employee_id', '=', self.employee.id),
                                      ('deduction_type', '=', dtype)], order='date, id')

    def _retained(self, code='LATE'):
        entries = self.env['hr.work.entry'].search([
            ('employee_id', '=', self.employee.id), ('code', '=', code)], order='date')
        return [(e.date, round(e.duration, 2)) for e in entries]

    # ── Politique par défaut ─────────────────────────────────────────────────

    def test_every_company_has_a_default_policy(self):
        company = self.env['res.company'].create({'name': 'Nouvelle société'})
        policy = company.attendance_policy_id
        self.assertTrue(policy)
        self.assertEqual(policy.company_id, company)
        self.assertEqual(policy.late_tolerance_minutes, 5)

    def test_employee_policy_overrides_company(self):
        self.assertEqual(self.employee._dz_attendance_policy(), self.policy)
        self.employee.attendance_policy_id = False
        self.assertEqual(self.employee._dz_attendance_policy(),
                         self.employee.company_id._dz_attendance_policy())

    # ── Tolérance ────────────────────────────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_tolerance_is_inclusive(self):
        """Tolérance 5 min : 5 min de retard sont tolérés, 6 min sont un retard."""
        self._day(date(2026, 10, 4), late_min=5)
        self.assertFalse(self._deductions())
        self._day(date(2026, 10, 5), late_min=6)
        self.assertEqual(self._deductions().mapped('date'), [date(2026, 10, 5)])

    # ── À chaque retard ──────────────────────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_manual_mode_waits_for_hr(self):
        self.policy.write({'late_rule': 'per_occurrence', 'late_cost': 'minutes'})
        self._day(date(2026, 10, 4), late_min=30)
        self.engine.sync(self.employee, date(2026, 10, 4), date(2026, 10, 7))
        ded = self._deductions()
        self.assertEqual(ded.status, 'to_validate')
        self.assertFalse(self._retained(), 'rien de retenu avant la décision RH')

        ded.action_validate()
        self.assertEqual(self._retained(), [(date(2026, 10, 4), 0.5)])
        self.assertEqual(ded.policy_id, self.policy)

        ded.action_refuse()
        self.assertFalse(self._retained(), 'refusée après coup : la retenue disparaît')

    @freeze_time('2026-10-08 12:00:00')
    def test_each_late_costs_half_a_day(self):
        self.policy.write({'late_rule': 'per_occurrence', 'late_cost': 'half_day'})
        self._day(date(2026, 10, 4), late_min=12)
        self._deductions().action_validate()
        self.assertEqual(self._retained(), [(date(2026, 10, 4), 4.0)])

    @freeze_time('2026-10-08 12:00:00')
    def test_no_retention_rule_keeps_lates_visible_only(self):
        self.policy.late_rule = 'none'
        self._day(date(2026, 10, 4), late_min=30)
        self._deductions().action_validate()
        self.assertEqual(self._deductions().status, 'validated')
        self.assertFalse(self._retained())

    @freeze_time('2026-10-08 12:00:00')
    def test_early_departure_full_day(self):
        self.policy.write({'early_rule': 'per_occurrence', 'early_cost': 'full_day'})
        self._day(date(2026, 10, 4), early_min=45)
        ded = self._deductions('early')
        self.assertEqual(len(ded), 1)
        ded.action_validate()
        self.assertEqual(self._retained('EARLY'), [(date(2026, 10, 4), 8.0)])

    # ── Cumul ────────────────────────────────────────────────────────────────

    @freeze_time('2026-10-20 12:00:00')
    def test_three_lates_cost_one_day(self):
        """3 retards = 1 jour : seul le 3e retard du mois coûte, une journée."""
        self.policy.write({'late_rule': 'cumulative', 'late_cumul_basis': 'count',
                           'late_cumul_threshold': 3, 'late_cumul_days': 1.0})
        for day in (4, 5, 6, 7):
            self._day(date(2026, 10, day), late_min=20)
        lates = self._deductions()
        lates.action_validate()
        self.assertEqual(self._retained(), [(date(2026, 10, 6), 8.0)])
        self.assertEqual(lates[2].retained_hours, 8.0)
        self.assertEqual(lates[0].retained_hours, 0.0)

        # Les RH refusent le 2e retard : c'est désormais le 4e qui franchit le seuil.
        lates[1].action_refuse()
        self.assertEqual(self._retained(), [(date(2026, 10, 7), 8.0)])

    @freeze_time('2026-10-20 12:00:00')
    def test_cumulated_minutes(self):
        """60 minutes cumulées = une demi-journée ; un retard peut franchir le seuil."""
        self.policy.write({'late_rule': 'cumulative', 'late_cumul_basis': 'minutes',
                           'late_cumul_threshold': 60, 'late_cumul_days': 0.5})
        self._day(date(2026, 10, 4), late_min=40)
        self._day(date(2026, 10, 5), late_min=30)    # 70 min : 1 seuil franchi
        self._day(date(2026, 10, 6), late_min=55)    # 125 min : 2e seuil franchi
        self._deductions().action_validate()
        self.assertEqual(self._retained(), [(date(2026, 10, 5), 4.0), (date(2026, 10, 6), 4.0)])

    @freeze_time('2026-11-20 12:00:00')
    def test_cumul_restarts_each_month(self):
        self.policy.write({'late_rule': 'cumulative', 'late_cumul_basis': 'count',
                           'late_cumul_threshold': 3, 'late_cumul_days': 1.0})
        for day in (date(2026, 10, 27), date(2026, 10, 28), date(2026, 11, 1)):
            self._day(day, late_min=20)
        self._deductions().action_validate()
        self.assertFalse(self._retained(), '2 retards en octobre + 1 en novembre : aucun seuil')

    # ── Mode automatique ─────────────────────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_auto_mode_applies_on_finished_days_only(self):
        self.policy.write({'mode': 'auto', 'late_rule': 'per_occurrence', 'late_cost': 'minutes'})
        self._day(date(2026, 10, 7), late_min=30)
        self._day(date(2026, 10, 8), late_min=30)    # aujourd'hui
        self.engine.sync(self.employee, date(2026, 10, 1), date(2026, 10, 8))
        by_day = {d.date: d.status for d in self._deductions()}
        self.assertEqual(by_day[date(2026, 10, 7)], 'validated')
        self.assertEqual(by_day[date(2026, 10, 8)], 'to_validate', 'journée pas finie')
        self.assertEqual(self._retained(), [(date(2026, 10, 7), 0.5)])

    @freeze_time('2026-10-08 12:00:00')
    def test_validated_payroll_month_is_frozen(self):
        self.policy.write({'late_rule': 'per_occurrence', 'late_cost': 'minutes'})
        self._day(date(2026, 10, 4), late_min=30)
        ded = self._deductions()
        ded.action_validate()
        ded.work_entry_id.action_validate()           # paie clôturée
        self.policy.late_cost = 'full_day'
        ded._sync_occurrence_entry()
        self.assertEqual(self._retained(), [(date(2026, 10, 4), 0.5)])

    # ── Arrondis et périodes ─────────────────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_late_rounded_up_to_started_slice(self):
        """Arrondi à 15 min : 7 min de retard comptent 15 min, 20 min en comptent 30."""
        self.policy.write({'late_rule': 'per_occurrence', 'late_cost': 'minutes',
                           'late_rounding_minutes': 15})
        self._day(date(2026, 10, 4), late_min=7)
        self._day(date(2026, 10, 5), late_min=20)
        self._deductions().action_validate()
        self.assertEqual(self._retained(), [(date(2026, 10, 4), 0.25), (date(2026, 10, 5), 0.5)])

    @freeze_time('2026-12-20 12:00:00')
    def test_cumul_over_a_quarter(self):
        """Cumul trimestriel : 3 retards répartis sur octobre-décembre = 1 jour."""
        self.policy.write({'late_rule': 'cumulative', 'late_cumul_basis': 'count',
                           'late_cumul_threshold': 3, 'late_cumul_days': 1.0,
                           'late_cumul_period': 'quarter'})
        for day in (date(2026, 10, 5), date(2026, 11, 2), date(2026, 12, 6)):
            self._day(day, late_min=20)
        self._deductions().action_validate()
        self.assertEqual(self._retained(), [(date(2026, 12, 6), 8.0)])

    def test_cumul_bounds(self):
        p = self.policy
        p.late_cumul_period = 'month'
        self.assertEqual(p._cumul_bounds(date(2026, 2, 14)), (date(2026, 2, 1), date(2026, 2, 28)))
        p.late_cumul_period = 'quarter'
        self.assertEqual(p._cumul_bounds(date(2026, 11, 14)), (date(2026, 10, 1), date(2026, 12, 31)))
        p.late_cumul_period = 'year'
        self.assertEqual(p._cumul_bounds(date(2026, 11, 14)), (date(2026, 1, 1), date(2026, 12, 31)))

    @freeze_time('2026-10-08 12:00:00')
    def test_half_day_is_half_of_that_day(self):
        """Une demi-journée de retenue = moitié des heures prévues CE jour-là
        (jeudi court de 4 h → 2 h), pas d'une journée type."""
        self.calendar.attendance_ids.filtered(
            lambda a: a.dayofweek == '3' and a.day_period == 'afternoon').unlink()
        self.policy.write({'late_rule': 'per_occurrence', 'late_cost': 'half_day'})
        base = datetime(2026, 10, 8) - timedelta(hours=1)    # jeudi 8 octobre
        self.env['hr.attendance'].create({
            'employee_id': self.employee.id,
            'check_in': base + timedelta(hours=8, minutes=30),
            'check_out': base + timedelta(hours=12)})
        self._deductions().action_validate()
        self.assertEqual(self._retained(), [(date(2026, 10, 8), 2.0)])

    # ── Absences à traiter (mode manuel) ─────────────────────────────────────

    def _absence_lines(self):
        return self.Deduction.search([('employee_id', '=', self.employee.id),
                                      ('deduction_type', '=', 'absence')], order='date')

    def _codes(self, day):
        return sorted(self.env['hr.work.entry'].search([
            ('employee_id', '=', self.employee.id), ('date', '=', day)]).mapped('code'))

    @freeze_time('2026-10-08 12:00:00')
    def test_absence_auto_mode_needs_no_decision(self):
        self.policy.absence_mode = 'auto'
        self.engine.sync(self.employee, date(2026, 10, 5), date(2026, 10, 5))
        self.assertEqual(self._codes(date(2026, 10, 5)), ['ABSENT'])
        self.assertFalse(self._absence_lines())

    @freeze_time('2026-10-08 12:00:00')
    def test_absence_manual_mode_waits_for_hr(self):
        self.policy.absence_mode = 'manual'
        day = date(2026, 10, 5)
        self.engine.sync(self.employee, day, day)
        line = self._absence_lines()
        self.assertEqual((line.status, line.duration), ('to_validate', 8.0))
        self.assertFalse(self._codes(day), 'rien de retenu avant la décision')

        line.action_validate()
        self.assertEqual(self._codes(day), ['ABSENT'])
        self.engine.sync(self.employee, day, day)          # recalcul : décision gardée
        self.assertEqual(self._codes(day), ['ABSENT'])
        self.assertEqual(len(self._absence_lines()), 1)

    @freeze_time('2026-10-08 12:00:00')
    def test_absence_justified_becomes_paid_entry(self):
        self.policy.absence_mode = 'manual'
        day = date(2026, 10, 6)
        self.engine.sync(self.employee, day, day)
        line = self._absence_lines()
        sick = self.env['hr.work.entry.type'].search([('code', '=', 'MALADIE')], limit=1)
        line.justification_type_id = sick
        line.action_justify()
        self.assertEqual(line.status, 'justified')
        self.assertEqual(self._codes(day), ['MALADIE'])

    @freeze_time('2026-10-08 12:00:00')
    def test_absence_tolerated_costs_nothing(self):
        self.policy.absence_mode = 'manual'
        day = date(2026, 10, 6)
        self.engine.sync(self.employee, day, day)
        self._absence_lines().action_refuse()
        self.assertFalse(self._codes(day))

    @freeze_time('2026-10-08 12:00:00')
    def test_late_punches_override_hr_decision(self):
        """Pointeuse hors ligne : l'absence retenue est remplacée par la présence
        réelle dès que les pointages arrivent (paie non validée)."""
        self.policy.absence_mode = 'manual'
        day = date(2026, 10, 7)
        self.engine.sync(self.employee, day, day)
        line = self._absence_lines()
        line.action_validate()
        self._day(day)
        self.assertEqual(line.status, 'refused')
        self.assertIn('pointages reçus', line.note)
        self.assertNotIn('ABSENT', self._codes(day))

    @freeze_time('2026-10-08 12:00:00')
    def test_pending_absence_disappears_when_punches_arrive(self):
        self.policy.absence_mode = 'manual'
        day = date(2026, 10, 7)
        self.engine.sync(self.employee, day, day)
        self.assertTrue(self._absence_lines())
        self._day(day)
        self.assertFalse(self._absence_lines())

    @freeze_time('2026-10-08 12:00:00')
    def test_justify_requires_a_reason(self):
        self.policy.absence_mode = 'manual'
        self.engine.sync(self.employee, date(2026, 10, 7), date(2026, 10, 7))
        with self.assertRaises(UserError):
            self._absence_lines().action_justify()

    # ── Sortie non pointée « à trancher par les RH » ─────────────────────────

    def _morning_only_open(self, day):
        """Matin complet (4 h), après-midi : entrée sans sortie."""
        base = datetime(day.year, day.month, day.day) - timedelta(hours=1)
        return self.env['hr.attendance'].create([
            {'employee_id': self.employee.id,
             'check_in': base + timedelta(hours=8), 'check_out': base + timedelta(hours=12)},
            {'employee_id': self.employee.id, 'check_in': base + timedelta(hours=13)},
        ])

    def _hours(self, day, code):
        return sum(self.env['hr.work.entry'].search([
            ('employee_id', '=', self.employee.id), ('date', '=', day),
            ('code', '=', code)]).mapped('duration'))

    @freeze_time('2026-10-08 12:00:00')
    def test_missing_punch_waits_for_hr(self):
        """En attente : seul le temps complet compte, 4 h manquantes à trancher."""
        self.policy.missing_punch_rule = 'hr'
        day = date(2026, 10, 7)
        self._morning_only_open(day)
        self.engine.sync(self.employee, day, day)
        line = self._deductions('missing')
        self.assertEqual((line.status, line.duration), ('to_validate', 4.0))
        self.assertAlmostEqual(self._hours(day, 'WORK100'), 4.0)
        self.assertFalse(self._hours(day, 'ABSENT'))

    @freeze_time('2026-10-08 12:00:00')
    def test_missing_punch_retained_or_tolerated(self):
        self.policy.missing_punch_rule = 'hr'
        day = date(2026, 10, 7)
        self._morning_only_open(day)
        self.engine.sync(self.employee, day, day)
        line = self._deductions('missing')
        line.action_validate()
        self.assertAlmostEqual(self._hours(day, 'ABSENT'), 4.0, msg='retenue : 4 h déduites')
        line.action_reset()
        line.action_refuse()
        self.assertFalse(self._hours(day, 'ABSENT'))
        self.assertAlmostEqual(self._hours(day, 'WORK100'), 8.0, msg='tolérée : journée comptée')
        # Un recalcul ne défait pas la décision.
        self.engine.sync(self.employee, day, day)
        self.assertAlmostEqual(self._hours(day, 'WORK100'), 8.0)

    @freeze_time('2026-10-08 12:00:00')
    def test_missing_punch_fixed_removes_line(self):
        """Les RH ajoutent la sortie : la ligne et sa prestation disparaissent."""
        self.policy.missing_punch_rule = 'hr'
        day = date(2026, 10, 7)
        atts = self._morning_only_open(day)
        self.engine.sync(self.employee, day, day)
        self._deductions('missing').action_validate()
        atts[1].check_out = atts[1].check_in + timedelta(hours=4)
        self.assertFalse(self._deductions('missing'))
        self.assertFalse(self._hours(day, 'ABSENT'))
        self.assertAlmostEqual(self._hours(day, 'WORK100'), 8.0)
