# -*- coding: utf-8 -*-
"""File « À corriger » et réglage des anomalies sans code."""
from datetime import datetime

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_to_fix')
class TestToFix(TransactionCase):

    def _queue(self):
        action = self.env.ref('hr_dz_attendance_anomaly.action_attendance_to_fix')
        from odoo.tools.safe_eval import safe_eval
        return self.env['hr.attendance'].search(safe_eval(action.domain))

    def test_missing_checkout_enters_queue_until_reviewed_or_fixed(self):
        employee = self.env['hr.employee'].create({'name': 'Oubli de sortie'})
        att = self.env['hr.attendance'].create({
            'employee_id': employee.id, 'check_in': datetime(2026, 10, 5, 7, 0)})
        self.assertIn(att, self._queue())
        att.action_mark_reviewed()
        self.assertNotIn(att, self._queue())
        att.check_out = datetime(2026, 10, 5, 7, 1)   # correction → réexaminé
        self.assertFalse(att.dz_reviewed)

    def test_threshold_rule_without_code(self):
        rule = self.env.ref('hr_dz_attendance_anomaly.anomaly_long_duration')
        rule.write({'detection_mode': 'standard', 'threshold_minutes': 600})
        self.assertIn('600 < worked_minutes', rule._effective_condition())
        rule.detection_mode = 'expert'
        self.assertEqual(rule._effective_condition(), rule.condition_code)
