# -*- coding: utf-8 -*-
from datetime import datetime
from odoo import _, models, fields, api


class NatsSubscription(models.Model):
    _name = 'nats.subscription'
    _description = 'NATS Subscription'
    _order = 'subject, handler_model'
    _rec_name = 'subject'

    server_id     = fields.Many2one('nats.server', ondelete='cascade', required=True, index=True)
    subject       = fields.Char(required=True, index=True)
    handler_model = fields.Char(string='Traitement (modèle)', readonly=True)
    is_jetstream  = fields.Boolean(string='JetStream', default=False, readonly=True)
    state         = fields.Selection([
        ('active',   'Active'),
        ('inactive', 'Inactive'),
    ], default='inactive', readonly=True)

    message_count   = fields.Integer(string='Messages', default=0, readonly=True)
    last_message_at = fields.Datetime(string='Dernier message', readonly=True)
    last_seen       = fields.Char(compute='_compute_last_seen', string='Dernière activité')

    _sub_unique = models.Constraint(
        'UNIQUE(server_id, subject, handler_model)',
        'Duplicate subscription entry.',
    )

    @api.depends('last_message_at')
    def _compute_last_seen(self):
        now = fields.Datetime.now()   # last_message_at est stocké en UTC
        for rec in self:
            if not rec.last_message_at:
                rec.last_seen = _('Jamais')
                continue
            delta = now - rec.last_message_at
            if delta.days > 0:
                rec.last_seen = _('il y a %s j', delta.days)
            elif delta.seconds >= 3600:
                rec.last_seen = _('il y a %s h', delta.seconds // 3600)
            elif delta.seconds >= 60:
                rec.last_seen = _('il y a %s min', delta.seconds // 60)
            else:
                rec.last_seen = _("à l'instant")