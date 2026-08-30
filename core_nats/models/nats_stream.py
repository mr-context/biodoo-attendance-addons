# -*- coding: utf-8 -*-
from odoo import models, fields


class NatsStream(models.Model):
    _name = 'nats.stream'
    _description = 'NATS JetStream Stream'
    _order = 'name'
    _rec_name = 'name'

    server_id    = fields.Many2one('nats.server', ondelete='cascade', required=True, index=True)
    name         = fields.Char(readonly=True)
    subjects     = fields.Char(string='Subjects', readonly=True)
    messages     = fields.Integer(string='Messages', readonly=True)
    bytes_stored = fields.Char(string='Size', readonly=True)
    consumers    = fields.Integer(string='Consumers', readonly=True)
    last_refresh = fields.Datetime(string='Last Refresh', readonly=True)