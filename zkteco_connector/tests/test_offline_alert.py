# -*- coding: utf-8 -*-
"""Pointeuse hors ligne : alerte persistante, une seule, close au retour."""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'dz_offline_alert')
class TestOfflineAlert(TransactionCase):

    def test_alert_once_then_closed(self):
        new_test_user(self.env, 'gest_pointeuses', groups='base.group_user,zkteco_connector.group_zkteco_manager')
        Device = self.env['zkteco.device']
        device = Device.create({'serial_number': 'TESTOFF1', 'state': 'approved',
                                'last_seen': fields.Datetime.now() - timedelta(hours=2)})
        Device._cron_mark_offline()
        Device._cron_mark_offline()
        alerts = device.activity_ids.filtered(lambda a: a.summary == 'Pointeuse hors ligne')
        self.assertEqual(device.state, 'offline')
        self.assertEqual(len(alerts), 1)
        device._close_offline_alert()
        self.assertFalse(device.activity_ids.filtered(lambda a: a.summary == 'Pointeuse hors ligne'))


@tagged('post_install', '-at_install', 'dz_catchup')
class TestCatchup(TransactionCase):

    def test_period_is_requested_from_each_device(self):
        from datetime import date
        device = self.env['zkteco.device'].create({'serial_number': 'TESTCATCH', 'state': 'approved'})
        sent = []
        self.patch(type(device), '_send_command', lambda self, cmd: sent.append((self.id, cmd)))
        wiz = self.env['zkteco.attlog.catchup.wizard'].create({
            'device_ids': [(6, 0, device.ids)], 'date_from': date(2026, 9, 1), 'date_to': date(2026, 9, 30)})
        wiz.action_request()
        self.assertEqual(sent, [(device.id, 'QUERY_ATTLOG START=2026-09-01T00:00:00 END=2026-09-30T23:59:59')])
