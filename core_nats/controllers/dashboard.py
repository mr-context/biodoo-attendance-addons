# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class NatsDashboardController(http.Controller):

    @http.route('/nats/dashboard/data', type='jsonrpc', auth='user', methods=['POST'])
    def dashboard_data(self):
        if not request.env.user.has_group('base.group_system'):
            return {'connected': False, 'error': 'Réservé à l\'administrateur.'}
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc:
            return {
                "connected": False,
                "url": "",
                "total_messages": 0,
                "rate_per_min": 0,
                "subscriptions": [],
                "recent_messages": [],
                "error": "Le bus n'est pas démarré : Paramètres → Bus des pointeuses (NATS) → Serveur → Démarrer.",
            }
        data = svc.get_dashboard_data()
        # Compteurs tenus en base : justes même avec plusieurs workers (la
        # mémoire du processus ne voit que ses propres messages).
        counts = {sub.subject: sub.message_count for sub in
                  request.env['nats.subscription'].sudo().search([])}
        for sub in data.get('subscriptions', []):
            sub['count'] = counts.get(sub['subject'], sub['count'])
        data['total_messages'] = sum(counts.values()) or data.get('total_messages', 0)
        data['dead_letters'] = request.env['nats.dead.letter'].sudo().search_count([('state', '=', 'new')])
        return data
