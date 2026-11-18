# -*- coding: utf-8 -*-
"""Fuseau : un pointage de 0 h 30 à Alger appartient au jour (et au mois) local."""
from datetime import date, datetime

import pytz

from odoo.tests.common import BaseCase, tagged
from odoo.addons.hr_dz_base.tools import dztime


@tagged('post_install', '-at_install', 'dz_time')
class TestDzTime(BaseCase):

    def test_month_bounds_follow_local_time(self):
        tz = pytz.timezone('Africa/Algiers')
        start, end = dztime.utc_bounds(date(2026, 11, 1), date(2026, 11, 30), tz)
        self.assertEqual(start, datetime(2026, 10, 31, 23, 0))   # 1er nov. 0 h à Alger
        self.assertEqual(end, datetime(2026, 11, 30, 23, 0))
        # 1er décembre 0 h 30 à Alger = 30 nov. 23 h 30 UTC : hors du mois de novembre
        self.assertFalse(start <= datetime(2026, 11, 30, 23, 30) < end)
        self.assertEqual(dztime.local_date(datetime(2026, 11, 30, 23, 30), tz), date(2026, 12, 1))

    def test_unknown_timezone_falls_back_to_algiers(self):
        class _Rec:
            tz = 'Mars/Olympus'
        self.assertEqual(dztime.tz_of(_Rec()).zone, 'Africa/Algiers')
