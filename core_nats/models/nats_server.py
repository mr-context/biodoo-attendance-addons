# -*- coding: utf-8 -*-
import threading
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class NatsServer(models.Model):
    _name = 'nats.server'
    _description = 'NATS Server'
    _rec_name = 'name'

    name = fields.Char(required=True, default='NATS Server')
    url  = fields.Char(
        string='URL',
        required=True,
        default='nats://localhost:4222',
        help='NATS server URL — the embedded NATS runs on port 4222 by default.',
    )
    auto_start = fields.Boolean(
        default=True,
        help='Start automatically when Odoo starts.',
    )
    state = fields.Selection(
        [('stopped', 'Stopped'), ('running', 'Running'), ('error', 'Error')],
        default='stopped',
        readonly=True,
    )

    subscription_ids = fields.One2many(
        'nats.subscription', 'server_id', string='Subscriptions', readonly=True,
    )
    subscription_count = fields.Integer(
        compute='_compute_subscription_count', string='Subscriptions'
    )
    stream_ids = fields.One2many(
        'nats.stream', 'server_id', string='Streams', readonly=True,
    )
    stream_count = fields.Integer(
        compute='_compute_stream_count', string='Streams'
    )

    is_connected = fields.Boolean(
        compute='_compute_live_status', string='Connected'
    )

    @api.depends('subscription_ids')
    def _compute_subscription_count(self):
        for rec in self:
            rec.subscription_count = len(rec.subscription_ids)

    @api.depends('stream_ids')
    def _compute_stream_count(self):
        for rec in self:
            rec.stream_count = len(rec.stream_ids)

    def _compute_live_status(self):
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        connected = bool(svc and svc.is_connected)
        for rec in self:
            rec.is_connected = connected

    # ── actions ───────────────────────────────────────────────────

    def action_start(self):
        self.ensure_one()
        from odoo.addons.core_nats.services.nats_service import (
            NatsService, get_service, set_service, _handler_registry,
        )

        from odoo.addons.core_nats.services.telemetry import register as _telemetry_register

        service = get_service()
        if service and service.is_running:
            self.state = 'running'
            self._sync_subscriptions(_handler_registry)
            _telemetry_register()
            return

        service = NatsService(
            url=self.url,
            registry=self.env.registry,
        )
        for model_name, subjects in _handler_registry.items():
            for subject in subjects:
                service.register_handler(subject, model_name)

        service.start()
        set_service(service)
        self.state = 'running'
        self._sync_subscriptions(_handler_registry)
        _telemetry_register()
        _logger.info(
            f"NATS service started: {self.url} ({len(_handler_registry)} handlers)"
        )

    def action_stop(self):
        self.ensure_one()
        from odoo.addons.core_nats.services.nats_service import get_service, set_service
        from odoo.addons.core_nats.services.telemetry import unregister as _telemetry_unregister
        service = get_service()
        if service:
            service.stop()
            set_service(None)
        _telemetry_unregister()
        self.state = 'stopped'
        self.subscription_ids.write({'state': 'inactive'})

    def action_restart(self):
        self.action_stop()
        self.action_start()

    def action_refresh_stats(self):
        """Pull in-memory stats from the running service into subscription records."""
        self.ensure_one()
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc:
            return
        stats = svc.get_stats()
        for sub in self.subscription_ids:
            st = stats.get(sub.subject)
            if st:
                sub.write({
                    'message_count':   st.get('count', sub.message_count),
                    'last_message_at': (
                        fields.Datetime.now() if st.get('last_at') else sub.last_message_at
                    ),
                })
        self.state = 'running' if svc.is_connected else 'error'

    def action_inspect_streams(self):
        """Query JetStream for all streams and refresh the stream_ids list."""
        self.ensure_one()
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_connected:
            raise UserError("NATS service is not connected — start it first.")

        stream_data = svc.get_streams()

        Stream = self.env['nats.stream'].sudo()
        Stream.search([('server_id', '=', self.id)]).unlink()

        for s in stream_data:
            Stream.create({
                'server_id':    self.id,
                'name':         s['name'],
                'subjects':     ', '.join(s['subjects']),
                'messages':     s['messages'],
                'bytes_stored': _human_bytes(s['bytes']),
                'consumers':    s['consumers'],
                'last_refresh': fields.Datetime.now(),
            })

        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # ── subscription sync ─────────────────────────────────────────

    def _sync_subscriptions(self, handler_registry: dict):
        Sub = self.env['nats.subscription'].sudo()
        for model_name, subjects in handler_registry.items():
            for subject in subjects:
                existing = Sub.search([
                    ('server_id',     '=', self.id),
                    ('subject',       '=', subject),
                    ('handler_model', '=', model_name),
                ], limit=1)
                if not existing:
                    Sub.create({
                        'server_id':     self.id,
                        'subject':       subject,
                        'handler_model': model_name,
                        'state':         'inactive',
                    })

    # ── auto-start on Odoo boot ───────────────────────────────────

    @api.model
    def _register_hook(self):
        super()._register_hook()

        def _auto_start():
            import time
            time.sleep(5)
            try:
                with self.env.registry.cursor() as cr:
                    env = api.Environment(cr, self.env.uid, {})
                    server = env['nats.server'].search(
                        [('auto_start', '=', True)], limit=1
                    )
                    if server:
                        server.action_start()
                        cr.commit()
                        _logger.info("NATS auto-start complete")
            except Exception as exc:
                _logger.error(f"NATS auto-start failed: {exc}", exc_info=True)

        threading.Thread(
            target=_auto_start, daemon=True, name="nats-autostart"
        ).start()


def _human_bytes(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
