"""Messages NATS mis à l'écart : malformés, ou en échec après plusieurs essais.

Au lieu de boucler sans fin (et de bloquer les suivants), un message qui ne
passe pas est rangé ici. L'administrateur voit pourquoi, corrige la cause
(PIN non rattaché, pointeuse non approuvée…) puis le relance.
"""
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class NatsDeadLetter(models.Model):
    _name = 'nats.dead.letter'
    _description = 'Message NATS en échec'
    _order = 'create_date desc, id desc'

    subject = fields.Char(string='Sujet', required=True, readonly=True, index=True)
    payload = fields.Text(string='Contenu', readonly=True)
    reason = fields.Char(string='Raison', readonly=True)
    deliveries = fields.Integer(string='Essais', readonly=True)
    state = fields.Selection([
        ('new', 'À traiter'), ('replayed', 'Relancé avec succès'), ('ignored', 'Ignoré'),
    ], string='État', default='new', required=True, index=True)
    last_error = fields.Char(string='Dernière erreur au relancement', readonly=True)

    def action_replay(self):
        """Relance le traitement du message dans Odoo (après correction de la cause)."""
        from odoo.addons.core_nats.services.nats_service import _handler_registry, nats_subject_matches
        for letter in self.filtered(lambda l: l.state == 'new'):
            try:
                payload = json.loads(letter.payload or '{}')
            except ValueError:
                letter.last_error = _('Contenu illisible : ne peut pas être relancé.')
                continue
            models_ = [m for pattern, names in _handler_registry.items()
                       if nats_subject_matches(pattern, letter.subject) for m in names]
            if not models_:
                letter.last_error = _('Aucun traitement ne correspond à ce sujet.')
                continue
            try:
                with self.env.cr.savepoint():
                    for model in set(models_):
                        reason = self.env[model]._nats_validate(letter.subject, payload)
                        if reason:
                            raise UserError(reason)
                        self.env[model].sudo().handle_nats_event(letter.subject, payload)
            except UserError as exc:
                letter.last_error = str(exc)
                continue
            letter.write({'state': 'replayed', 'last_error': False})

    def action_ignore(self):
        self.write({'state': 'ignored'})
