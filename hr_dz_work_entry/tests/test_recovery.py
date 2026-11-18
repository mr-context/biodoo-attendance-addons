"""
Récupérations : jour de repos, férié ou heures sup. travaillés → repos à prendre.

Même calendrier que le moteur : dimanche-jeudi 8 h-12 h / 13 h-17 h à Alger.
Le samedi 10 octobre 2026 est un jour de repos.
"""

from datetime import date, datetime

from freezegun import freeze_time

from odoo.tests import TransactionCase, tagged

SATURDAY = date(2026, 10, 10)
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


@tagged('post_install', '-at_install', 'dz_work_entry', 'dz_recovery')
class TestRecovery(TransactionCase):

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
            'name': 'Dim-Jeu (récup)', 'tz': 'Africa/Algiers', 'attendance_ids': lines,
            'hours_per_day': 8.0,
        })
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Employé récup', 'resource_calendar_id': cls.calendar.id, 'tz': 'Africa/Algiers',
        })
        cls.employee.version_id.write({
            'contract_date_start': date(2026, 1, 1),
            'resource_calendar_id': cls.calendar.id,
            'work_entry_source': 'attendance',
            'wage': 50000,
        })
        cls.policy = cls.env['hr.attendance.policy'].create({
            'name': 'Politique récup (test)', 'company_id': cls.employee.company_id.id,
            'recovery_enabled': True,
        })
        cls.employee.attendance_policy_id = cls.policy
        cls.engine = cls.env['hr.dz.work.entry.engine']
        cls.Recovery = cls.env['hr.dz.recovery']

    # ── Outils ───────────────────────────────────────────────────────────────

    def _entries(self, day):
        return self.env['hr.work.entry'].search(
            [('employee_id', '=', self.employee.id), ('date', '=', day)])

    def _by_code(self, day):
        return {e.code: e.duration for e in self._entries(day)}

    def _work(self, day, hours_in, hours_out, overtime=None, rate=1.5, **line_vals):
        """Pointage (heure d'Alger) + heures sup. approuvées, maîtrisées par le test."""
        att = self.env['hr.attendance'].create({
            'employee_id': self.employee.id,
            'check_in': datetime(day.year, day.month, day.day, hours_in - 1, 0),
            'check_out': datetime(day.year, day.month, day.day, hours_out - 1, 0),
        })
        Line = self.env['hr.attendance.overtime.line']
        Line.search([('employee_id', '=', self.employee.id), ('date', '=', day)]).unlink()
        line = Line.browse()
        if overtime:
            line = Line.create(dict({
                'employee_id': self.employee.id, 'date': day, 'duration': overtime,
                'amount_rate': rate, 'status': 'approved'}, **line_vals))
        return att, line

    def _right(self, day=SATURDAY):
        return self.Recovery.search([('employee_id', '=', self.employee.id), ('date', '=', day),
                                     ('state', '!=', 'cancelled')])

    # ── Ce qui ouvre droit ───────────────────────────────────────────────────

    @freeze_time('2026-10-15 12:00:00')
    def test_disabled_policy_pays_overtime(self):
        self.policy.recovery_enabled = False
        self._work(SATURDAY, 8, 16, overtime=8.0)
        self.assertEqual(self._by_code(SATURDAY), {'OT50': 8.0})
        self.assertFalse(self._right())

    @freeze_time('2026-10-15 12:00:00')
    def test_saturday_mixed_by_default(self):
        """Mixte : la majoration 50 % se paie (MAJ50), les 8 h vont au compteur,
        sous forme d'une attribution « Récupération » valable 30 jours."""
        self._work(SATURDAY, 8, 16, overtime=8.0)
        self.assertEqual(self._by_code(SATURDAY), {'MAJ50': 8.0})
        right = self._right()
        self.assertEqual((right.kind, right.treatment, right.hours), ('rest_day', 'mixed', 8.0))
        self.assertEqual(right.expiry_date, date(2026, 11, 9))
        alloc = right.allocation_id
        self.assertEqual(alloc.state, 'validate')
        self.assertAlmostEqual(alloc.number_of_hours_display, 8.0)
        self.assertEqual(alloc.date_to, date(2026, 11, 9))

    @freeze_time('2026-10-15 12:00:00')
    def test_saturday_fully_recovered_includes_premium(self):
        """Récupéré : rien n'est payé, 8 h à 150 % = 12 h de repos."""
        self.policy.recovery_rest_day_mode = 'recovered'
        self._work(SATURDAY, 8, 16, overtime=8.0)
        self.assertEqual(self._by_code(SATURDAY), {'RECACQ': 8.0})
        self.assertAlmostEqual(self._right().hours, 12.0)

    @freeze_time('2026-10-15 12:00:00')
    def test_flat_basis_half_day(self):
        """Forfait : 3 h (sous le seuil de 4 h) = une demi-journée."""
        self.policy.recovery_basis = 'flat'
        self._work(SATURDAY, 8, 11, overtime=3.0)
        self.assertAlmostEqual(self._right().hours, 4.0)

    @freeze_time('2026-10-15 12:00:00')
    def test_weekday_overtime_cap(self):
        """Heures sup. de semaine : payées jusqu'à 2 h dans le mois, au-delà
        majoration payée et temps en repos."""
        self.policy.write({'recovery_overtime_mode': 'mixed', 'recovery_overtime_cap': 2.0})
        self._work(MONDAY, 8, 19, overtime=3.0)
        codes = self._by_code(MONDAY)
        self.assertAlmostEqual(codes.get('OT50'), 2.0)
        self.assertAlmostEqual(codes.get('MAJ50'), 1.0)
        self._work(TUESDAY, 8, 18, overtime=2.0)
        codes = self._by_code(TUESDAY)
        self.assertNotIn('OT50', codes, 'plafond du mois déjà atteint lundi')
        self.assertAlmostEqual(codes.get('MAJ50'), 2.0)
        self.assertAlmostEqual(self._right(MONDAY).hours, 1.0)
        self.assertAlmostEqual(self._right(TUESDAY).hours, 2.0)

    @freeze_time('2026-10-15 12:00:00')
    def test_hr_choice_on_one_overtime_line(self):
        """Les RH choisissent « payée » pour ce samedi précis : pas de repos."""
        _att, line = self._work(SATURDAY, 8, 16, overtime=8.0)
        self.assertTrue(self._right())
        line.dz_compensation = 'paid'
        self.assertEqual(self._by_code(SATURDAY), {'OT50': 8.0})
        self.assertFalse(self._right(), 'droit annulé : plus rien en repos')

    # ── La réalité l'emporte ─────────────────────────────────────────────────

    @freeze_time('2026-10-15 12:00:00')
    def test_corrected_punch_withdraws_unused_right(self):
        att, line = self._work(SATURDAY, 8, 16, overtime=8.0)
        right = self._right()
        alloc = right.allocation_id
        line.unlink()
        att.unlink()
        self.assertEqual(right.state, 'cancelled')
        self.assertFalse(alloc.exists(), 'attribution supprimée : rien de pris')

    @freeze_time('2026-10-15 12:00:00')
    def test_taken_recovery_is_paid_leave_and_never_erased(self):
        """Repos pris = prestation RECUP (payée, pas une absence). Si le droit
        disparaît ensuite, il est gardé pour ce qui a été pris et signalé."""
        att, line = self._work(SATURDAY, 8, 16, overtime=8.0)
        right = self._right()
        leave = self.env['hr.leave'].create({
            'employee_id': self.employee.id,
            'holiday_status_id': self.Recovery._leave_type().id,
            'request_date_from': date(2026, 10, 14), 'request_date_to': date(2026, 10, 14),
        })
        leave.sudo().action_approve()
        if leave.state != 'validate':
            leave.sudo().action_validate()
        self.engine.sync(self.employee, date(2026, 10, 14), date(2026, 10, 14))
        self.assertEqual(self._by_code(date(2026, 10, 14)), {'RECUP': 8.0})
        right.invalidate_recordset()
        self.assertAlmostEqual(right.taken_hours, 8.0)

        line.unlink()
        att.unlink()
        self.assertNotEqual(right.state, 'cancelled')
        self.assertTrue(right.needs_review)
        self.assertAlmostEqual(right.hours, 8.0)

    # ── Échéance ─────────────────────────────────────────────────────────────

    @freeze_time('2026-10-15 12:00:00')
    def test_expiry_hr_decides_then_pays(self):
        self._work(SATURDAY, 8, 16, overtime=8.0)
        right = self._right()
        self.Recovery._cron_expiry(today=date(2026, 11, 10))
        self.assertEqual(right.state, 'to_decide')
        right.action_pay()
        self.assertEqual(right.state, 'paid')
        self.assertAlmostEqual(right.settled_hours, 8.0)
        self.assertEqual(right.allocation_id.state, 'refuse', 'plus rien à prendre')

    @freeze_time('2026-10-15 12:00:00')
    def test_expiry_extend(self):
        self._work(SATURDAY, 8, 16, overtime=8.0)
        right = self._right()
        self.Recovery._cron_expiry(today=date(2026, 11, 10))
        right.action_extend()
        self.assertEqual(right.state, 'active')
        self.assertEqual(right.expiry_date, date(2026, 12, 9))
        self.assertEqual(right.allocation_id.date_to, date(2026, 12, 9))

    @freeze_time('2026-10-15 12:00:00')
    def test_expiry_automatic_policies(self):
        self.policy.recovery_expiry_action = 'lose'
        self._work(SATURDAY, 8, 16, overtime=8.0)
        right = self._right()
        self.Recovery._cron_expiry(today=date(2026, 11, 10))
        self.assertEqual((right.state, right.settled_hours), ('lost', 8.0))

    @freeze_time('2026-10-15 12:00:00')
    def test_reminder_before_expiry(self):
        self.employee.leave_manager_id = self.env.user
        self._work(SATURDAY, 8, 16, overtime=8.0)
        right = self._right()
        self.Recovery._cron_expiry(today=date(2026, 11, 3))
        self.assertTrue(right.reminded)
        self.assertTrue(right.activity_ids)
        self.assertEqual(right.state, 'active')

    @freeze_time('2026-10-15 12:00:00')
    def test_balance(self):
        self._work(SATURDAY, 8, 16, overtime=8.0)
        self.assertAlmostEqual(self.Recovery.balance_hours(self.employee, date(2026, 10, 31)), 8.0)
        self.assertAlmostEqual(self.Recovery.balance_hours(self.employee, date(2026, 10, 9)), 0.0)

    @freeze_time('2026-10-15 12:00:00')
    def test_portal_summary(self):
        self._work(SATURDAY, 8, 16, overtime=8.0)
        summary = self.Recovery.portal_summary(self.employee, date(2026, 10, 15))
        self.assertAlmostEqual(summary['days'], 1.0)
        self.assertEqual(summary['next_expiry'], date(2026, 11, 9))
        self.assertAlmostEqual(summary['next_hours'], 8.0)
        other = self.env['hr.employee'].create({'name': 'Sans récup'})
        other.attendance_policy_id = self.env['hr.attendance.policy'].create({
            'name': 'Sans récup', 'company_id': other.company_id.id})
        self.assertIsNone(self.Recovery.portal_summary(other), 'pas de carte vide')
