# -*- coding: utf-8 -*-
"""Licence/slot side of zkteco.device — kept in a SEPARATE inherited model so it
never collides with the device's attendance/anomaly logic.

Trust model: Odoo only EXPRESSES intent (approve/release) over NATS; the bridge
(the sealed binary, which the customer cannot modify) is the authority that
grants or denies a licence slot — keyed by MAC, not the firmware-changeable SN.
This module publishes the intents and reflects the bridge's verdict; it does NOT
enforce the cap itself (a customer could patch Odoo).
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ZktecoDeviceLicense(models.Model):
    _inherit = 'zkteco.device'

    # A device approved by the operator but parked over the licence cap by the
    # bridge — waiting for a freed slot or a plan upgrade. Never hard-rejected.
    state = fields.Selection(
        selection_add=[('held', 'En attente de slot (licence)')],
        ondelete={'held': 'set default'},
    )

    license_reason = fields.Char(string='Statut licence', readonly=True, copy=False)
    license_used   = fields.Integer(string='Slots utilisés', readonly=True, copy=False)
    license_max    = fields.Integer(string='Slots licence', readonly=True, copy=False)

    # ── NATS intent publishing (Odoo → bridge) ───────────────────────────────

    def _license_publish(self, action, payload):
        """Publish a licence intent on zkteco.ta.license.<action>.<sn>.
        Returns False (and logs) if NATS is down — the periodic reconcile cron
        re-asserts approved devices, so a transient outage self-heals."""
        self.ensure_one()
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_running:
            _logger.warning("[zkteco_lic] NATS arrêté — intent '%s' pour %s différé",
                            action, self.serial_number)
            return False
        return svc.publish_js_sync(
            f'zkteco.ta.license.{action}.{self.serial_number}', payload)

    def action_request_license_slot(self):
        """Ask the bridge to grant a licence slot to this device (operator
        approval). The bridge replies on zkteco.ta.licresult.<sn>."""
        for d in self:
            if not d.mac_address:
                _logger.warning("[zkteco_lic] %s sans MAC — approbation licence différée "
                                "(en attente des infos device)", d.serial_number)
                continue
            d._license_publish('approve', {'mac': d.mac_address, 'sn': d.serial_number})

    def _license_release(self):
        """Free this device's licence slot on the bridge (removal/replacement)."""
        for d in self:
            if d.mac_address:
                d._license_publish('release', {'mac': d.mac_address})

    def unlink(self):
        # Free the slot before the record disappears, so a held device can be
        # promoted in its place (the BioTime "delete to free a slot" model).
        for d in self:
            if d.state in ('approved', 'held'):
                d._license_release()
        return super().unlink()

    @api.model
    def _license_reconcile_all(self):
        """Re-assert every approved/held device to the bridge, then publish the
        FULL approved-MAC list as a declarative `sync` intent (event-driven, on
        bridge.online):
        - migrates existing approved devices on first deployment;
        - self-heals after a NATS/bridge outage;
        - the `sync` makes the bridge release any slot Odoo doesn't know about
          (ghost slots from release intents lost while NATS was down).
        The bridge is idempotent and caps the count, so this is always safe."""
        devices = self.search([
            ('state', 'in', ('approved', 'held')),
            ('mac_address', '!=', False),
        ])
        for d in devices:
            d._license_publish('approve', {'mac': d.mac_address, 'sn': d.serial_number})
        _logger.info("[zkteco_lic] réconciliation: %d device(s) ré-asserté(s) au bridge",
                     len(devices))
        self._license_publish_sync(devices.mapped('mac_address'))

    @api.model
    def _license_publish_sync(self, macs):
        """Publish the declarative full-set reconciliation (BioTime's
        comparison pattern): the bridge releases every approved/held slot whose
        MAC is absent from `macs`. Subject carries no SN — it targets the whole
        slot set."""
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_running:
            _logger.warning("[zkteco_lic] NATS arrêté — sync licence différé")
            return False
        ok = svc.publish_js_sync('zkteco.ta.license.sync', {'macs': sorted(macs)})
        if ok:
            _logger.info("[zkteco_lic] sync licence publié (%d MAC)", len(macs))
        return ok
