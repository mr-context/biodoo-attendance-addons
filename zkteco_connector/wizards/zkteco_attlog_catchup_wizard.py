# -*- coding: utf-8 -*-
"""Récupérer les pointages d'une période, sur une ou plusieurs pointeuses.

Sert après une panne longue (Odoo arrêté plus de 7 jours, bus vidé) ou une
pointeuse remplacée : la pointeuse renvoie ses pointages de la période ; les
doublons sont écartés par le sas (unique pointeuse + PIN + horodatage).
"""
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

MAX_DAYS = 400   # une pointeuse ne garde de toute façon pas plus d'un an


class ZktecoAttlogCatchupWizard(models.TransientModel):
    _name = 'zkteco.attlog.catchup.wizard'
    _description = 'Récupérer les pointages d\'une période'

    device_ids = fields.Many2many('zkteco.device', string='Pointeuses', required=True,
                                  domain=[('state', 'in', ('approved', 'offline'))])
    date_from = fields.Date(string='Du', required=True,
                            default=lambda self: fields.Date.context_today(self) - timedelta(days=30))
    date_to = fields.Date(string='Au', required=True, default=fields.Date.context_today)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'zkteco.device' and 'device_ids' in fields_list:
            res['device_ids'] = [(6, 0, self.env.context.get('active_ids', []))]
        return res

    def action_request(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("La date de début est après la date de fin."))
        if (self.date_to - self.date_from).days > MAX_DAYS:
            raise UserError(_("Période trop longue (%s jours au plus).", MAX_DAYS))
        # Heures locales de la pointeuse : début du premier jour, fin du dernier.
        start = datetime.combine(self.date_from, time.min).strftime('%Y-%m-%dT%H:%M:%S')
        end = datetime.combine(self.date_to, time(23, 59, 59)).strftime('%Y-%m-%dT%H:%M:%S')
        for device in self.device_ids:
            device._send_command(f'QUERY_ATTLOG START={start} END={end}')
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('Pointages demandés'),
                'message': _('%(n)s pointeuse(s) vont renvoyer leurs pointages du %(a)s au %(b)s ; '
                             'les doublons sont écartés automatiquement.',
                             n=len(self.device_ids), a=self.date_from.strftime('%d/%m/%Y'),
                             b=self.date_to.strftime('%d/%m/%Y')),
                'type': 'success', 'next': {'type': 'ir.actions.act_window_close'},
            },
        }
