# -*- coding: utf-8 -*-
"""Réagit au cycle de vie du bridge (bridge → Odoo).

Le bridge est l'autorité sur les slots : « Odoo subit l'état du bridge ». Quand
le bridge (re)démarre et réactive sa licence, il publie zkteco.ta.bridge.online.
On en profite pour re-synchroniser : Odoo ré-asserte ses devices approved/held,
le bridge répond ses verdicts autoritaires (zkteco.ta.licresult.<sn>), et Odoo
s'aligne. Aucun cron : c'est piloté par l'évènement « le bridge est prêt ».
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ZktecoBridgeHandler(models.AbstractModel):
    _name = 'zkteco.bridge.handler'
    _inherit = 'nats.handler'
    _description = 'ZKTeco Bridge Lifecycle NATS Handler'

    _nats_subjects = ['zkteco.ta.bridge.online']

    @api.model
    def handle_nats_event(self, subject, payload):
        try:
            fp = payload.get('fingerprint') if isinstance(payload, dict) else None
            _logger.info("[zkteco_bridge] bridge online (fp=%s) → réconciliation des slots", fp)
            Device = self.env['zkteco.device'].sudo()
            Device._license_reconcile_all()
            # Le bridge est de retour : rejouer les commandes RH mises en outbox
            # pendant qu'il/NATS était indisponible (voir _send_command soft).
            Device._republish_pending_commands()
        except Exception as exc:
            _logger.error("[zkteco_bridge] erreur réconciliation '%s': %s", subject, exc, exc_info=True)