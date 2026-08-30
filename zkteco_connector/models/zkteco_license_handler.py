# -*- coding: utf-8 -*-
"""Consumes the bridge's authoritative licence verdict (bridge → Odoo).

The bridge replies to every approve/replace intent on zkteco.ta.licresult.<sn>
with {mac, sn, state, granted, reason, used, max}. We reflect it on the device:
a device is shown 'approved' ONLY when the bridge actually granted it a slot;
otherwise it is parked 'held' with the reason — the cap can never be bypassed
from Odoo, because Odoo merely mirrors the trusted bridge's decision.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ZktecoLicenseHandler(models.AbstractModel):
    _name = 'zkteco.license.handler'
    _inherit = 'nats.handler'
    _description = 'ZKTeco Licence Verdict NATS Handler'

    _nats_subjects = ['zkteco.ta.licresult.>']

    @api.model
    def handle_nats_event(self, subject, payload):
        try:
            if not isinstance(payload, dict):
                return
            sn = payload.get('sn') or subject.split('.')[-1]
            mac = (payload.get('mac') or '').strip()

            Device = self.env['zkteco.device'].sudo()
            device = Device.search([('serial_number', '=', sn)], limit=1)
            if not device and mac:
                device = Device.search([('mac_address', '=', mac)], limit=1)

            # Release confirmation (unlink or sync_orphan): the device usually
            # no longer exists in Odoo — just log the closed loop. If it DOES
            # still exist (state drift), don't park it 'held': re-request a slot
            # so the bridge re-arbitrates.
            if payload.get('state') == 'released':
                if device:
                    _logger.warning("[zkteco_lic] slot libéré côté bridge mais device encore "
                                    "présent sn=%s — re-demande de slot", sn)
                    device.action_request_license_slot()
                else:
                    _logger.info("[zkteco_lic] release confirmé par le bridge sn=%s mac=%s "
                                 "(reason=%s)", sn, mac, payload.get('reason'))
                return

            if not device:
                _logger.warning("[zkteco_lic] verdict pour device inconnu sn=%s mac=%s", sn, mac)
                return

            granted = bool(payload.get('granted'))
            device.write({
                # Authoritative: approved iff the bridge granted a slot, else held.
                'state':          'approved' if granted else 'held',
                'license_reason': payload.get('reason') or '',
                'license_used':   int(payload.get('used') or 0),
                'license_max':    int(payload.get('max') or 0),
            })
            _logger.info("[zkteco_lic] verdict sn=%s granted=%s → %s (%s/%s)",
                         sn, granted, device.state, device.license_used, device.license_max)
        except Exception as exc:
            _logger.error("[zkteco_lic] erreur verdict '%s': %s", subject, exc, exc_info=True)
