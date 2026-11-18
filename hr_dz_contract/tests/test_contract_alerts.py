# -*- coding: utf-8 -*-
"""Alertes de contrat : destinataire réel, CDD échu signalé une fois."""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'dz_contract')
class TestContractAlerts(TransactionCase):

    def test_expired_cdd_alert_goes_to_an_hr_user(self):
        hr_manager = new_test_user(self.env, 'rh_alertes', groups='base.group_user,hr.group_hr_manager')
        today = fields.Date.context_today(self.env['hr.version'])
        employee = self.env['hr.employee'].create({'name': 'CDD échu'})
        version = employee.version_id
        version.write({'contract_date_start': today - relativedelta(months=6),
                       'contract_date_end': today - relativedelta(days=3),
                       'hr_responsible_id': hr_manager.id})
        Version = self.env['hr.version']
        Version._cron_contract_end_alerts()
        Version._cron_contract_end_alerts()   # pas de doublon
        acts = version.activity_ids.filtered(lambda a: a.summary == 'CDD échu sans décision')
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts.user_id, hr_manager)
