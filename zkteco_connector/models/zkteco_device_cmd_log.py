# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ZktecoDeviceCmdLog(models.Model):
    _name = 'zkteco.device.cmd.log'
    _description = 'ZKTeco — Journal des commandes device'
    _order = 'create_date desc'
    _rec_name = 'cmd'

    device_id     = fields.Many2one('zkteco.device', string='Device', index=True, ondelete='cascade')
    cmd           = fields.Char(string='Commande sémantique', readonly=True)
    wire_cmd      = fields.Char(string='Commande ADMS', readonly=True,
                                help='Commande brute retournée par le device dans cmdresult')
    bridge_cmd_id = fields.Integer(string='ID bridge', readonly=True,
                                   help='ID numérique assigné par le bridge Go dans sa file interne')
    return_code   = fields.Integer(string='Code retour', readonly=True)
    is_error      = fields.Boolean(string='Erreur', compute='_compute_is_error', store=True)

    state = fields.Selection([
        ('requested',       'Demandée'),
        ('pending_publish', 'En attente (NATS indispo)'),
        ('published',       'Publiée'),
        ('ok',              'OK'),
        ('error',           'Erreur'),
        ('expired',         'Expirée'),
    ], string='État', default='requested', readonly=True, index=True)

    cmd_uuid     = fields.Char(string='UUID', readonly=True, index=True, copy=False,
                               help='UUID de corrélation entre Odoo et le bridge')
    requested_by = fields.Many2one('res.users', string='Demandé par', readonly=True, ondelete='set null')
    requested_at = fields.Datetime(string='Demandée le', readonly=True)
    published_at = fields.Datetime(string='Publiée le', readonly=True)
    result_at    = fields.Datetime(string='Résultat le', readonly=True)
    create_date  = fields.Datetime(string='Date', readonly=True)

    @api.depends('return_code')
    def _compute_is_error(self):
        for r in self:
            r.is_error = r.return_code != 0