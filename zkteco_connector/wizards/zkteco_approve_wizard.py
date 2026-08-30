# -*- coding: utf-8 -*-
import logging
from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ZktecoApproveWizard(models.TransientModel):
    _name = 'zkteco.approve.wizard'
    _description = 'ZKTeco Device Approval Wizard'

    device_id = fields.Many2one('zkteco.device', required=True, readonly=True)

    device_serial = fields.Char(related='device_id.serial_number', readonly=True)
    device_name   = fields.Char(related='device_id.device_name',   readonly=True)
    device_ip     = fields.Char(related='device_id.ip_address',    readonly=True)

    def action_approve(self):
        self.ensure_one()
        device = self.device_id

        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc:
            raise UserError("Le service NATS n'est pas démarré. Démarrez-le d'abord.")

        device.sudo().write({
            'state':       'approved',
            'approved_by':  self.env.uid,
            'approved_date': fields.Datetime.now(),
        })
        _logger.info(f"[zkteco] device approved: {device.serial_number}")

        device._send_command('INFO')

        # Ask the bridge (the licence authority) for a slot. If the cap is full,
        # the bridge replies "held" and the verdict handler downgrades the state
        # with a reason — Odoo cannot self-approve beyond the licence.
        device.sudo().action_request_license_slot()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Device approuvé',
                'message': f"Device {device.serial_number} approuvé — demande de slot licence envoyée.",
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }