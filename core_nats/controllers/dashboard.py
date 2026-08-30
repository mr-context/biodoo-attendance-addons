# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class NatsDashboardController(http.Controller):

    @http.route('/nats/dashboard/data', type='jsonrpc', auth='user', methods=['POST'])
    def dashboard_data(self):
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
                "error": "Service not started — go to Technical > NATS Servers and click Start.",
            }
        return svc.get_dashboard_data()
