"""Présences en Gantt : la vue est servie et les heures hors planning grisées."""

from datetime import datetime

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_gantt')
class TestAttendanceGantt(TransactionCase):

    def test_view_and_data(self):
        view = self.env.ref('hr_dz_attendance_gantt.hr_attendance_view_gantt')
        fields = self.env['hr.attendance'].get_views([(view.id, 'gantt')])['models']['hr.attendance']['fields']
        for name in ('check_in', 'check_out', 'worked_hours', 'color'):
            self.assertIn(name, fields)
        employee = self.env['hr.employee'].create({'name': 'Gantt présence'})
        self.env['hr.attendance'].create({'employee_id': employee.id,
                                          'check_in': datetime(2026, 10, 14, 7, 0),
                                          'check_out': datetime(2026, 10, 14, 11, 0)})
        data = self.env['hr.attendance'].get_gantt_data(
            [('employee_id', '=', employee.id)], ['employee_id'],
            {'check_in': {}, 'check_out': {}, 'worked_hours': {}},
            unavailability_fields=['employee_id'],
            start_date='2026-10-13 23:00:00', stop_date='2026-10-14 23:00:00', scale='day')
        self.assertEqual(len(data['records']), 1)
        gaps = data['unavailabilities']['employee_id'][employee.id]
        self.assertTrue(gaps, 'la nuit est hors planning')

    def test_attendances_open_on_list(self):
        action = self.env['ir.actions.act_window']._for_xml_id('hr_attendance.hr_attendance_action')
        modes = [mode for _view, mode in action['views']]
        self.assertEqual(modes[0], 'list', "Présences s'ouvre sur la liste")
        self.assertIn('gantt', modes)

    def test_attendance_gantt_is_read_only(self):
        """Les pointages viennent des pointeuses : pas de glisser, ni de création
        ou suppression depuis le Gantt (correction par le formulaire, tracée)."""
        arch = self.env.ref('hr_dz_attendance_gantt.hr_attendance_view_gantt').arch
        for attr in ('disable_drag_drop="1"', 'create="0"', 'delete="0"'):
            self.assertIn(attr, arch)
