# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ZktecoWipeWizard(models.TransientModel):
    _name = 'zkteco.wipe.wizard'
    _description = 'Vider le device ZKTeco'

    device_id     = fields.Many2one('zkteco.device', required=True, readonly=True)
    device_serial = fields.Char(related='device_id.serial_number', readonly=True)
    device_name   = fields.Char(related='device_id.device_name',   readonly=True)
    device_ip     = fields.Char(related='device_id.ip_address',    readonly=True)

    confirmation  = fields.Char(string='Tapez le N° Série pour confirmer')

    is_confirmed  = fields.Boolean(compute='_compute_confirmed')

    @api.depends('confirmation', 'device_serial')
    def _compute_confirmed(self):
        for w in self:
            w.is_confirmed = bool(
                w.confirmation and w.confirmation.strip() == w.device_serial
            )

    def action_wipe(self):
        self.ensure_one()
        if (self.confirmation or '').strip() != self.device_serial:
            raise UserError("Le N° Série saisi ne correspond pas.")

        device = self.device_id
        device._send_command('WIPE_USERS')
        _logger.info(f"[zkteco] wipe requested on {device.serial_number}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Vidage lancé',
                'message': f'Suppression de tous les utilisateurs de {device.display_name or device.serial_number}.',
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }