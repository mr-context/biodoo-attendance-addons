"""Prestations en Gantt : vue, données et jours hors planning grisés."""

from datetime import date, datetime

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_gantt')
class TestWorkEntryGantt(TransactionCase):

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
            'name': 'Dim-Jeu (gantt)', 'tz': 'Africa/Algiers', 'attendance_ids': lines})
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Employé gantt', 'resource_calendar_id': cls.calendar.id,
            'tz': 'Africa/Algiers'})
        cls.employee.version_id.write({
            'contract_date_start': date(2026, 1, 1),
            'resource_calendar_id': cls.calendar.id,
            'work_entry_source': 'calendar',
        })

    def test_view_and_action(self):
        view = self.env.ref('hr_dz_work_entry_gantt.hr_work_entry_view_gantt')
        result = self.env['hr.work.entry'].get_views([(view.id, 'gantt')])
        self.assertIn('date', result['models']['hr.work.entry']['fields'])
        action = self.env['ir.actions.act_window']._for_xml_id(
            'hr_dz_work_entry.hr_work_entry_action_payroll')
        self.assertEqual(action['views'][0][1], 'gantt', 'le Gantt s\'ouvre en premier')

    def test_unavailability_greys_rest_days(self):
        """Semaine du 4 au 10 octobre 2026 (dimanche → samedi) à Alger :
        vendredi 9 et samedi 10 entièrement indisponibles, dimanche non."""
        start, stop = datetime(2026, 10, 3, 23, 0), datetime(2026, 10, 10, 23, 0)
        gaps = self.env['hr.work.entry']._gantt_unavailability(
            'employee_id', self.employee.ids, start, stop, 'week')[self.employee.id]

        def covered(day_start, day_stop):
            return any(g['start'] <= day_start and g['stop'] >= day_stop for g in gaps)

        self.assertTrue(covered('2026-10-08 23:00:00', '2026-10-09 23:00:00'), 'vendredi')
        self.assertTrue(covered('2026-10-09 23:00:00', '2026-10-10 23:00:00'), 'samedi')
        self.assertFalse(covered('2026-10-03 23:00:00', '2026-10-04 23:00:00'), 'dimanche travaillé')

    def test_gantt_data_rows_per_employee(self):
        wtype = self.env.ref('hr_work_entry.work_entry_type_attendance')
        self.env['hr.work.entry'].create({
            'name': 'P', 'employee_id': self.employee.id, 'date': date(2026, 10, 5),
            'duration': 8, 'work_entry_type_id': wtype.id})
        data = self.env['hr.work.entry'].get_gantt_data(
            [('employee_id', '=', self.employee.id)], ['employee_id'],
            {'date': {}, 'duration': {}, 'work_entry_type_id': {'fields': {'display_name': {}}}},
            unavailability_fields=['employee_id'],
            start_date='2026-09-30 23:00:00', stop_date='2026-10-31 23:00:00', scale='month')
        self.assertEqual(data['groups'][0]['employee_id'][0], self.employee.id)
        self.assertEqual(len(data['records']), 1)
        self.assertIn(self.employee.id, data['unavailabilities']['employee_id'])

    def _codes(self, day):
        return sorted(self.env['hr.work.entry'].search([
            ('employee_id', '=', self.employee.id), ('date', '=', day),
            ('state', '!=', 'cancelled')]).mapped('code'))

    def test_set_type_replaces_the_day(self):
        """Sélection de deux jours → un type : une seule prestation par jour,
        saisie RH, de la durée prévue."""
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        days = ['2026-10-05', '2026-10-06']
        result = self.env['hr.work.entry'].dz_gantt_set_type(
            [[self.employee.id, d] for d in days], leave.id)
        self.assertEqual((result['done'], result['locked']), (2, 0))
        for d in days:
            entries = self.env['hr.work.entry'].search([
                ('employee_id', '=', self.employee.id), ('date', '=', d)])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries.work_entry_type_id, leave)
            self.assertEqual(entries.dz_origin, 'manual')
            self.assertAlmostEqual(entries.duration, 8.0)

    def test_set_type_skips_validated_and_out_of_contract(self):
        wtype = self.env.ref('hr_work_entry.work_entry_type_attendance')
        entry = self.env['hr.work.entry'].create({
            'name': 'P', 'employee_id': self.employee.id, 'date': date(2026, 10, 7),
            'duration': 8, 'work_entry_type_id': wtype.id})
        entry.state = 'validated'
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        result = self.env['hr.work.entry'].dz_gantt_set_type(
            [[self.employee.id, '2026-10-07'], [self.employee.id, '2025-06-01']], leave.id)
        self.assertEqual((result['done'], result['locked']), (0, 2))
        self.assertEqual(entry.work_entry_type_id, wtype)

    def test_reset_removes_hr_entries(self):
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        Entry = self.env['hr.work.entry']
        Entry.dz_gantt_set_type([[self.employee.id, '2026-10-05']], leave.id)
        result = Entry.dz_gantt_reset([[self.employee.id, '2026-10-05']])
        self.assertEqual(result['done'], 1)
        self.assertFalse(Entry.search([('employee_id', '=', self.employee.id),
                                       ('date', '=', '2026-10-05'), ('dz_origin', '=', 'manual')]))

    def test_set_type_uses_planned_hours_not_overtime(self):
        """Régression : 8 h + 2 h sup. ce jour-là → le congé posé dure 8 h, pas 10 h."""
        Entry = self.env['hr.work.entry']
        work = self.env.ref('hr_work_entry.work_entry_type_attendance')
        overtime = self.env['hr.work.entry.type'].search([('code', '=', 'OT50')], limit=1)
        Entry.create([
            {'name': 'P', 'employee_id': self.employee.id, 'date': date(2026, 10, 5),
             'duration': 8, 'work_entry_type_id': work.id},
            {'name': 'HS', 'employee_id': self.employee.id, 'date': date(2026, 10, 5),
             'duration': 2, 'work_entry_type_id': overtime.id},
        ])
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        Entry.dz_gantt_set_type([[self.employee.id, '2026-10-05']], leave.id)
        entries = Entry.search([('employee_id', '=', self.employee.id), ('date', '=', '2026-10-05')])
        self.assertEqual(len(entries), 1)
        self.assertAlmostEqual(entries.duration, 8.0)

    def test_employee_without_entries_has_a_row(self):
        """L'absent est celui qu'on cherche : sous contrat sur la période, il a sa ligne."""
        data = self.env['hr.work.entry'].get_gantt_data(
            [], ['employee_id'], {'date': {}},
            start_date='2026-09-30 23:00:00', stop_date='2026-10-31 23:00:00', scale='month')
        rows = {g['employee_id'][0]: g['__record_ids'] for g in data['groups'] if g['employee_id']}
        self.assertIn(self.employee.id, rows)
        self.assertEqual(rows[self.employee.id], [])

    def test_employee_filter_adds_no_row(self):
        other = self.env['hr.employee'].create({'name': 'Autre'})
        data = self.env['hr.work.entry'].get_gantt_data(
            [('employee_id', '=', other.id)], ['employee_id'], {'date': {}},
            start_date='2026-09-30 23:00:00', stop_date='2026-10-31 23:00:00', scale='month')
        shown = {g['employee_id'][0] for g in data['groups'] if g['employee_id']}
        self.assertNotIn(self.employee.id, shown, 'un filtre sur les employés est respecté')

    def test_greyed_days_follow_contract_schedule(self):
        """Les jours grisés suivent le planning du CONTRAT (comme le calcul de
        paie), pas celui de la fiche employé."""
        seven_days = self.env['resource.calendar'].create({
            'name': '7/7', 'tz': 'Africa/Algiers', 'attendance_ids': [
                (0, 0, {'name': d, 'dayofweek': d, 'hour_from': 8, 'hour_to': 16,
                        'day_period': 'morning'}) for d in '0123456']})
        self.employee.resource_calendar_id = seven_days
        self.employee.version_id.resource_calendar_id = self.calendar
        start, stop = datetime(2026, 10, 3, 23, 0), datetime(2026, 10, 10, 23, 0)
        gaps = self.env['hr.work.entry']._gantt_unavailability(
            'employee_id', self.employee.ids, start, stop, 'week')[self.employee.id]
        friday = ('2026-10-08 23:00:00', '2026-10-09 23:00:00')
        self.assertTrue(any(g['start'] <= friday[0] and g['stop'] >= friday[1] for g in gaps),
                        'vendredi : repos au planning du contrat')

    # ── Remplacement sûr ────────────────────────────────────────────────────

    def test_rest_days_skipped_unless_asked(self):
        """Congé posé du dimanche au samedi : vendredi et samedi (repos) ne
        reçoivent rien, sauf choix explicite."""
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        week = [[self.employee.id, '2026-10-%02d' % d] for d in range(4, 11)]
        Entry = self.env['hr.work.entry']
        result = Entry.dz_gantt_set_type(week, leave.id)
        self.assertEqual((result['done'], result['off']), (5, 2))
        self.assertFalse(Entry.search([('employee_id', '=', self.employee.id),
                                       ('date', 'in', ['2026-10-09', '2026-10-10'])]))
        result = Entry.dz_gantt_set_type(week, leave.id, True)
        self.assertEqual(result['done'], 7)

    def test_preview_flags_overtime_that_would_be_lost(self):
        overtime = self.env['hr.work.entry.type'].search([('code', '=', 'OT50')], limit=1)
        self.env['hr.work.entry'].create({
            'name': 'HS', 'employee_id': self.employee.id, 'date': date(2026, 10, 5),
            'duration': 2, 'work_entry_type_id': overtime.id})
        preview = self.env['hr.work.entry'].dz_gantt_preview(
            [[self.employee.id, '2026-10-05'], [self.employee.id, '2026-10-06']])
        self.assertEqual((preview['todo'], preview['sensitive'], preview['replaced']), (2, 1, 1))

    def test_undo_restores_what_was_replaced(self):
        Entry = self.env['hr.work.entry']
        overtime = self.env['hr.work.entry.type'].search([('code', '=', 'OT50')], limit=1)
        Entry.create({'name': 'HS', 'employee_id': self.employee.id, 'date': date(2026, 10, 5),
                      'duration': 2, 'work_entry_type_id': overtime.id, 'dz_origin': 'manual'})
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        result = Entry.dz_gantt_set_type([[self.employee.id, '2026-10-05']], leave.id)
        change = self.env['hr.dz.work.entry.change'].browse(result['change_id'])
        self.assertEqual((change.action, change.day_count), ('set_type', 1))
        self.assertIn('Employé gantt', change.summary)
        Entry.dz_gantt_undo(change.id)
        self.assertTrue(change.undone)
        day = Entry.search([('employee_id', '=', self.employee.id), ('date', '=', '2026-10-05')])
        self.assertIn(overtime, day.work_entry_type_id, 'les heures sup. sont revenues')
        self.assertNotIn(leave, day.work_entry_type_id)

    def test_hr_manager_can_replace(self):
        """Un gestionnaire RH (non administrateur) peut remplacer des journées,
        alors qu'Odoo réserve la suppression de prestations à l'administrateur.
        (Un simple « Responsable RH » ne peut pas créer de prestation dans Odoo :
        il ne lit pas les dates de contrat.)"""
        officer = self.env['res.users'].create({
            'name': 'RH', 'login': 'rh_gantt_test',
            'group_ids': [(6, 0, [self.env.ref('hr.group_hr_manager').id])]})
        leave = self.env.ref('hr_work_entry.work_entry_type_leave')
        Entry = self.env['hr.work.entry'].with_user(officer)
        result = Entry.dz_gantt_set_type([[self.employee.id, '2026-10-05']], leave.id)
        self.assertEqual(result['done'], 1)
        # … et l'annuler (régression : la suppression était refusée).
        Entry.dz_gantt_undo(result['change_id'])
        self.assertTrue(self.env['hr.dz.work.entry.change'].browse(result['change_id']).undone)

    def test_cancelled_entries_are_not_shown(self):
        wtype = self.env.ref('hr_work_entry.work_entry_type_attendance')
        entry = self.env['hr.work.entry'].create({
            'name': 'X', 'employee_id': self.employee.id, 'date': date(2026, 10, 5),
            'duration': 8, 'work_entry_type_id': wtype.id})
        entry.state = 'cancelled'
        data = self.env['hr.work.entry'].get_gantt_data(
            [('employee_id', '=', self.employee.id)], ['employee_id'], {'date': {}})
        self.assertNotIn(entry.id, [r['id'] for r in data['records']])
