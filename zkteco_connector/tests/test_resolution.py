"""Du pointage brut à la présence : appariement, rafales, postes de nuit,
équivalence pointeuse / portail, verrou par journée.

Horaire de référence : Alger (UTC+1), dimanche-jeudi 8 h-12 h / 13 h-17 h ;
poste de nuit 22 h-6 h.
"""
from datetime import datetime, timedelta

from odoo.tests import TransactionCase, tagged


def utc(day, hour, minute=0):
    """Heure d'Alger → UTC naïf."""
    return datetime(2026, 10, day, hour, minute) - timedelta(hours=1)


@tagged('post_install', '-at_install', 'dz_attlog')
class TestResolution(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        day_lines, night_lines = [], []
        for d in ('6', '0', '1', '2', '3'):
            day_lines += [(0, 0, {'name': 'M', 'dayofweek': d, 'hour_from': 8, 'hour_to': 12, 'day_period': 'morning'}),
                          (0, 0, {'name': 'A', 'dayofweek': d, 'hour_from': 13, 'hour_to': 17, 'day_period': 'afternoon'})]
            night_lines += [(0, 0, {'name': 'Nuit', 'dayofweek': d, 'hour_from': 22, 'hour_to': 30, 'day_period': 'afternoon'})]
        Cal = cls.env['resource.calendar']
        cls.day_cal = Cal.create({'name': 'Jour', 'tz': 'Africa/Algiers', 'attendance_ids': day_lines})
        cls.night_cal = Cal.create({'name': 'Nuit', 'tz': 'Africa/Algiers', 'attendance_ids': night_lines})
        cls.device = cls.env['zkteco.device'].create({'serial_number': 'TEST0001', 'state': 'approved'})
        cls.Attlog = cls.env['zkteco.device.attlog']
        cls.day_emp = cls.env['hr.employee'].create({'name': 'Jour', 'zkteco_pin': '501', 'tz': 'Africa/Algiers',
                                                     'resource_calendar_id': cls.day_cal.id})
        cls.night_emp = cls.env['hr.employee'].create({'name': 'Nuit', 'zkteco_pin': '502', 'tz': 'Africa/Algiers',
                                                       'resource_calendar_id': cls.night_cal.id})

    def _device_punch(self, employee, ts, status=255):
        self.Attlog._store_or_process('TEST0001', employee.zkteco_pin, ts, status, '1')

    def _atts(self, employee):
        return self.env['hr.attendance'].search([('employee_id', '=', employee.id)], order='check_in')

    def test_day_pairs_and_burst_is_ignored(self):
        for ts in (utc(5, 8), utc(5, 8, 0) + timedelta(seconds=20), utc(5, 12), utc(5, 13), utc(5, 17)):
            self._device_punch(self.day_emp, ts)
        atts = self._atts(self.day_emp)
        self.assertEqual([(a.check_in, a.check_out) for a in atts],
                         [(utc(5, 8), utc(5, 12)), (utc(5, 13), utc(5, 17))])
        self.assertEqual(self.Attlog.search_count([('employee_id', '=', self.day_emp.id), ('state', '=', 'ignored')]), 1)

    def test_night_shift_is_one_attendance(self):
        """22 h → 6 h le lendemain : UNE présence, pas une entrée et une sortie orphelines."""
        self._device_punch(self.night_emp, utc(5, 22))
        self._device_punch(self.night_emp, utc(6, 6))
        atts = self._atts(self.night_emp)
        self.assertEqual(len(atts), 1)
        self.assertEqual((atts.check_in, atts.check_out), (utc(5, 22), utc(6, 6)))
        self.assertFalse(self.Attlog.search([('employee_id', '=', self.night_emp.id), ('state', '=', 'anomaly')]))

    def test_portal_and_device_give_same_result(self):
        """Même suite de pointages → mêmes présences, qu'ils viennent du portail ou d'une pointeuse."""
        other = self.env['hr.employee'].create({'name': 'Portail', 'zkteco_pin': '503', 'tz': 'Africa/Algiers',
                                                'resource_calendar_id': self.day_cal.id})
        times = [utc(7, 8), utc(7, 12), utc(7, 13), utc(7, 17)]
        for ts in times:
            self._device_punch(self.day_emp, ts)
            self.Attlog._stage_portal(other, ts)
        dev = [(a.check_in, a.check_out) for a in self._atts(self.day_emp) if a.check_in >= utc(7, 0)]
        por = [(a.check_in, a.check_out) for a in self._atts(other)]
        self.assertEqual(dev, por)

    def test_work_day_of_night_punch(self):
        self.assertEqual(self.Attlog._work_day_of(self.night_emp, utc(6, 5)).day, 5,
                         "la sortie de 5 h appartient à la nuit commencée la veille")
        self.assertEqual(self.Attlog._work_day_of(self.day_emp, utc(6, 5)).day, 6)
