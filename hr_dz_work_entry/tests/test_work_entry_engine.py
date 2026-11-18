"""
Moteur de prestations : planning comparé aux pointages.

Calendrier : octobre 2026, horaire dimanche-jeudi 8 h-12 h / 13 h-17 h à Alger
(UTC+1, sans heure d'été). Le 1er octobre 2026 est un jeudi ; les 9-10
(vendredi-samedi) sont chômés.
"""

from datetime import date, datetime

from freezegun import freeze_time

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_work_entry')
class TestWorkEntryEngine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        lines = []
        for day in ('6', '0', '1', '2', '3'):  # dimanche → jeudi
            lines += [
                (0, 0, {'name': 'Matin', 'dayofweek': day, 'hour_from': 8, 'hour_to': 12,
                        'day_period': 'morning'}),
                (0, 0, {'name': 'Pause', 'dayofweek': day, 'hour_from': 12, 'hour_to': 13,
                        'day_period': 'lunch'}),
                (0, 0, {'name': 'Après-midi', 'dayofweek': day, 'hour_from': 13, 'hour_to': 17,
                        'day_period': 'afternoon'}),
            ]
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'Dim-Jeu Alger (test)', 'tz': 'Africa/Algiers', 'attendance_ids': lines,
        })
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Employé moteur', 'resource_calendar_id': cls.calendar.id, 'tz': 'Africa/Algiers',
        })
        cls.employee.version_id.write({
            'contract_date_start': date(2026, 1, 1),
            'resource_calendar_id': cls.calendar.id,
            'work_entry_source': 'attendance',
            'wage': 50000,
        })
        cls.engine = cls.env['hr.dz.work.entry.engine']
        cls.Type = cls.env['hr.work.entry.type']
        cls.absent = cls.Type.search([('code', '=', 'ABSENT')], limit=1)
        cls.work = cls.engine._types()['work']

    # ── Outils ───────────────────────────────────────────────────────────────

    def _entries(self, day=None, employee=None):
        domain = [('employee_id', '=', (employee or self.employee).id)]
        if day:
            domain.append(('date', '=', day))
        return self.env['hr.work.entry'].search(domain, order='date, id')

    def _codes(self, day, employee=None):
        return sorted(self._entries(day, employee).mapped('code'))

    def _punch(self, day, hour_in, hour_out, employee=None):
        """Pointage en heure d'Alger (UTC+1) → stockage UTC naïf."""
        return self.env['hr.attendance'].create({
            'employee_id': (employee or self.employee).id,
            'check_in': datetime(day.year, day.month, day.day, hour_in - 1, 0),
            'check_out': datetime(day.year, day.month, day.day, hour_out - 1, 0)
            if hour_out else False,
        })

    # ── Absences ─────────────────────────────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_absence_on_past_working_days_only(self):
        """Jour ouvré terminé sans pointage → absence de 8 h ; ni le jour même,
        ni le week-end, ni l'avenir."""
        self.engine.sync(self.employee, date(2026, 10, 4), date(2026, 10, 11))
        for day in (4, 5, 6, 7):
            entry = self._entries(date(2026, 10, day))
            self.assertEqual(entry.mapped('code'), ['ABSENT'], 'jour %s' % day)
            self.assertAlmostEqual(entry.duration, 8.0)  # pause déjeuner exclue
            self.assertEqual(entry.dz_origin, 'auto')
        self.assertFalse(self._entries(date(2026, 10, 8)), "aujourd'hui : pas encore absent")
        self.assertFalse(self._entries(date(2026, 10, 9)), 'vendredi chômé')
        self.assertFalse(self._entries(date(2026, 10, 11)), 'avenir')

    @freeze_time('2026-10-08 12:00:00')
    def test_presence_replaces_absence_when_punches_arrive_late(self):
        """Pointeuse hors ligne : l'absence posée est remplacée par la présence
        réelle dès que les pointages arrivent, sans aucune action."""
        day = date(2026, 10, 5)
        self.engine.sync(self.employee, day, day)
        self.assertEqual(self._codes(day), ['ABSENT'])

        self._punch(day, 8, 16)  # arrivée tardive des pointages
        entries = self._entries(day)
        self.assertEqual(entries.mapped('code'), [self.work.code])
        self.assertAlmostEqual(entries.duration, 8.0)

        # Relancer ne change plus rien (idempotent).
        self.engine.sync(self.employee, day, day)
        self.assertEqual(self._entries(day), entries)

    @freeze_time('2026-10-08 12:00:00')
    def test_missing_checkout_follows_policy(self):
        """Entrée sans sortie : la politique décide (journée prévue, absence, rien)."""
        day = date(2026, 10, 6)
        self._punch(day, 8, None)
        policy = self.employee._dz_attendance_policy()

        policy.missing_punch_rule = 'planned'
        self.engine.sync(self.employee, day, day)
        self.assertEqual(self._codes(day), [self.work.code])
        self.assertAlmostEqual(self._entries(day).duration, 8.0)

        policy.missing_punch_rule = 'absent'
        self.engine.sync(self.employee, day, day)
        self.assertEqual(self._codes(day), ['ABSENT'])

        policy.missing_punch_rule = 'ignore'
        self.engine.sync(self.employee, day, day)
        self.assertFalse(self._entries(day))

    @freeze_time('2026-10-08 12:00:00')
    def test_absence_delay(self):
        """Constater l'absence après 3 jours : le 6 (J-2) n'est pas encore absent."""
        self.employee._dz_attendance_policy().absence_after_days = 3
        self.engine.sync(self.employee, date(2026, 10, 4), date(2026, 10, 7))
        self.assertEqual(self._codes(date(2026, 10, 5)), ['ABSENT'])
        self.assertFalse(self._entries(date(2026, 10, 6)))
        self.assertFalse(self._entries(date(2026, 10, 7)))

    @freeze_time('2026-10-08 12:00:00')
    def test_overtime_rate_comes_from_odoo_rules(self):
        """Heures sup. approuvées : une prestation par taux des règles d'Odoo."""
        day = date(2026, 10, 5)
        self._punch(day, 8, 16)
        Line = self.env['hr.attendance.overtime.line']
        Line.search([('employee_id', '=', self.employee.id)]).unlink()
        Line.create([
            {'employee_id': self.employee.id, 'date': day, 'duration': 2.0,
             'amount_rate': 1.5, 'status': 'approved'},
            {'employee_id': self.employee.id, 'date': day, 'duration': 1.0,
             'amount_rate': 1.85, 'status': 'approved'},
            {'employee_id': self.employee.id, 'date': day, 'duration': 4.0,
             'amount_rate': 2.0, 'status': 'refused'},
        ])
        entries = self._entries(day)
        by_rate = {e.work_entry_type_id.overtime_rate: e.duration
                   for e in entries if e.work_entry_type_id.overtime_rate > 1}
        self.assertEqual(by_rate, {1.5: 2.0, 1.85: 1.0}, 'refusées ignorées, taux 185 % créé')
        work = entries.filtered(lambda e: e.work_entry_type_id == self.work)
        self.assertAlmostEqual(work.duration, 8.0 - 3.0)

    def test_planned_intervals_single_definition(self):
        """Le prévu : pause exclue, poste de nuit sur deux jours."""
        cal = self.calendar
        day = date(2026, 10, 5)
        self.assertEqual(self.engine._planned_hours(self.employee, cal, day), 8.0)
        night = self.env['resource.calendar'].create({
            'name': 'Nuit (test)', 'tz': 'Africa/Algiers', 'attendance_ids': [
                (0, 0, {'name': 'Nuit', 'dayofweek': '0', 'hour_from': 22, 'hour_to': 30,
                        'day_period': 'morning'})]})
        intervals = self.engine._planned_intervals(self.employee, night, day)
        self.assertEqual(intervals, [(datetime(2026, 10, 5, 22, 0), datetime(2026, 10, 6, 6, 0))])

    # ── Ce qui appartient aux RH ─────────────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_hr_correction_is_never_overwritten(self):
        day = date(2026, 10, 6)
        self.engine.sync(self.employee, day, day)
        absence = self._entries(day)
        mission = self.Type.search([('code', '=', 'CA')], limit=1)
        absence.write({'work_entry_type_id': mission.id})
        self.assertEqual(absence.dz_origin, 'manual')

        self.engine.sync(self.employee, day, day)
        self._punch(day, 8, 16)  # même des pointages tardifs ne la remplacent pas
        self.assertEqual(self._entries(day), absence)
        self.assertEqual(absence.code, 'CA')

    @freeze_time('2026-10-08 12:00:00')
    def test_cancelled_absence_stays_cancelled(self):
        """Les RH annulent (archivent) une absence : elle n'est pas recréée."""
        day = date(2026, 10, 6)
        self.engine.sync(self.employee, day, day)
        self._entries(day).action_archive()
        self.engine.sync(self.employee, day, day)
        self.assertFalse(self._entries(day))

    @freeze_time('2026-10-08 12:00:00')
    def test_manual_entry_owns_the_day(self):
        day = date(2026, 10, 7)
        manual = self.env['hr.work.entry'].create({
            'employee_id': self.employee.id, 'date': day, 'duration': 8,
            'work_entry_type_id': self.Type.search([('code', '=', 'MALADIE')], limit=1).id,
        })
        self.assertEqual(manual.dz_origin, 'manual')
        self.engine.sync(self.employee, day, day)
        self.assertEqual(self._entries(day), manual)

    @freeze_time('2026-10-08 12:00:00')
    def test_validated_entries_are_frozen(self):
        day = date(2026, 10, 5)
        self.engine.sync(self.employee, day, day)
        absence = self._entries(day)
        absence.action_validate()
        self.assertEqual(absence.state, 'validated')
        self._punch(day, 8, 16)
        self.assertEqual(self._entries(day), absence, 'mois clôturé : rien ne bouge')

    @freeze_time('2026-10-08 12:00:00')
    def test_force_regenerate_keeps_hr_work(self):
        day_auto, day_hr = date(2026, 10, 5), date(2026, 10, 6)
        self.engine.sync(self.employee, day_auto, day_hr)
        hr_entry = self._entries(day_hr)
        hr_entry.write({'duration': 4})
        self.env['hr.work.entry.compute.wizard'].create({
            'date_from': day_auto, 'date_to': day_hr,
            'employee_ids': [(6, 0, self.employee.ids)], 'force_regenerate': True,
        }).action_compute()
        self.assertEqual(self._entries(day_hr), hr_entry)
        self.assertAlmostEqual(hr_entry.duration, 4.0)
        self.assertEqual(self._codes(day_auto), ['ABSENT'])

    # ── Jours, fériés, congés, contrat ───────────────────────────────────────

    @freeze_time('2026-10-08 12:00:00')
    def test_punch_after_midnight_belongs_to_local_day(self):
        """0 h 30 à Alger le 6 = 23 h 30 UTC le 5 : la présence est au 6."""
        self.env['hr.attendance'].create({
            'employee_id': self.employee.id,
            'check_in': datetime(2026, 10, 5, 23, 30),
            'check_out': datetime(2026, 10, 6, 7, 30),
        })
        self.engine.sync(self.employee, date(2026, 10, 5), date(2026, 10, 6))
        self.assertEqual(self._codes(date(2026, 10, 6)), [self.work.code])
        self.assertEqual(self._codes(date(2026, 10, 5)), ['ABSENT'])

    @freeze_time('2026-10-08 12:00:00')
    def test_public_holiday_on_its_local_day_only(self):
        holiday_type = self.Type.create({'name': 'Férié (test)', 'code': 'FERIE_T'})
        self.env['resource.calendar.leaves'].create({
            'name': 'Férié test',
            # Journée du 6 octobre à Alger, stockée en UTC.
            'date_from': datetime(2026, 10, 5, 23, 0),
            'date_to': datetime(2026, 10, 6, 22, 59, 59),
            'work_entry_type_id': holiday_type.id,
        })
        self.engine.sync(self.employee, date(2026, 10, 5), date(2026, 10, 7))
        self.assertEqual(self._codes(date(2026, 10, 6)), ['FERIE_T'])
        self.assertEqual(self._codes(date(2026, 10, 5)), ['ABSENT'], 'la veille reste un jour normal')
        self.assertEqual(self._codes(date(2026, 10, 7)), ['ABSENT'])

    @freeze_time('2026-10-08 12:00:00')
    def test_validated_leave_is_not_an_absence(self):
        ca = self.Type.search([('code', '=', 'CA')], limit=1)
        leave_type = self.env['hr.leave.type'].create({
            'name': 'Congé (test)', 'requires_allocation': False,
            'leave_validation_type': 'no_validation', 'work_entry_type_id': ca.id,
        })
        self.env['hr.leave'].create({
            'employee_id': self.employee.id, 'holiday_status_id': leave_type.id,
            'request_date_from': date(2026, 10, 5), 'request_date_to': date(2026, 10, 6),
        })
        self.engine.sync(self.employee, date(2026, 10, 4), date(2026, 10, 7))
        self.assertEqual(self._codes(date(2026, 10, 5)), ['CA'])
        self.assertEqual(self._codes(date(2026, 10, 6)), ['CA'])
        self.assertEqual(self._codes(date(2026, 10, 4)), ['ABSENT'])
        self.assertEqual(self._codes(date(2026, 10, 7)), ['ABSENT'])

    @freeze_time('2026-10-08 12:00:00')
    def test_no_absence_outside_contract(self):
        self.employee.version_id.contract_date_start = date(2026, 10, 6)
        self.engine.sync(self.employee, date(2026, 10, 4), date(2026, 10, 7))
        self.assertFalse(self._entries(date(2026, 10, 4)), 'avant embauche')
        self.assertFalse(self._entries(date(2026, 10, 5)), 'avant embauche')
        self.assertEqual(self._codes(date(2026, 10, 6)), ['ABSENT'])

    @freeze_time('2026-10-08 12:00:00')
    def test_schedule_source_employee_is_left_to_odoo(self):
        """Personnel dispensé de pointage (source Horaire) : le moteur ne crée rien."""
        self.employee.version_id.work_entry_source = 'calendar'
        self.engine.sync(self.employee, date(2026, 10, 4), date(2026, 10, 7))
        self.assertFalse(self._entries())

    @freeze_time('2026-10-08 12:00:00')
    def test_odoo_generation_goes_through_engine(self):
        """La génération d'Odoo (tâche de nuit) passe par le moteur : absences
        jusqu'à hier, rien après aujourd'hui."""
        self.employee.version_id.generate_work_entries(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(self._codes(date(2026, 10, 7)), ['ABSENT'])
        self.assertFalse(self._entries(date(2026, 10, 12)))

    def test_dz_overtime_ruleset_is_adopted(self):
        """Mise à jour : un contrat sur le jeu d'Odoo (taux 1) passe au jeu
        algérien ; un jeu choisi par le client est conservé."""
        odoo_default = self.env.ref('hr_attendance.hr_attendance_default_ruleset')
        dz = self.env.ref('hr_dz_work_entry.overtime_ruleset_dz')
        custom = self.env['hr.attendance.overtime.ruleset'].create({'name': 'Maison (test)'})
        other = self.env['hr.employee'].create({'name': 'Autre (test)'})
        self.employee.version_id.ruleset_id = odoo_default
        other.version_id.ruleset_id = custom
        self.env['hr.version']._dz_adopt_dz_overtime_ruleset()
        self.assertEqual(self.employee.version_id.ruleset_id, dz)
        self.assertEqual(other.version_id.ruleset_id, custom)
        self.assertEqual(self.env['hr.employee'].create({'name': 'Nouveau'}).version_id.ruleset_id, dz)
