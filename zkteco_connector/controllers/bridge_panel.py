# -*- coding: utf-8 -*-
"""Expose l'état live du bridge (panneau :8090) à l'UI Odoo, via NATS req/reply.

Le HTTP :8090 du bridge est en loopback (injoignable depuis un Odoo distant). Le
seul lien qui marche dans les deux topologies (co-localisé / bridge derrière NAT)
c'est NATS, déjà partagé. On interroge les responders request/reply du bridge :
  - zkteco.ta.bridge.status  → PanelStatus (licence + slots + lien serveur)
  - zkteco.ta.bridge.devices → inventaire des pointeuses
L'appel NATS reste CÔTÉ SERVEUR Odoo (jamais le navigateur).
"""
from odoo import http
from odoo.http import request


class ZktecoBridgePanelController(http.Controller):

    @http.route('/zkteco/bridge/status', type='jsonrpc', auth='user', methods=['POST'])
    def bridge_status(self):
        # Réservé aux utilisateurs Odoo internes (le panneau expose tenant/fingerprint).
        if not request.env.user._is_internal():
            return {'reachable': False, 'error': "Accès refusé."}

        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_running:
            return {'reachable': False, 'error': "Service NATS arrêté côté Odoo."}

        status = svc.request_sync('zkteco.ta.bridge.status', b'', timeout=2.0)
        if not isinstance(status, dict):
            # Timeout = aucun bridge n'a répondu sur ce NATS.
            return {'reachable': False, 'error': "Bridge hors ligne (aucune réponse)."}

        devices = svc.request_sync('zkteco.ta.bridge.devices', b'', timeout=2.0)
        return {
            'reachable': True,
            'status': status,
            'devices': devices if isinstance(devices, list) else [],
        }