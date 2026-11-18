# -*- coding: utf-8 -*-
"""
nats.handler — AbstractModel that connector modules inherit.

Usage in a connector module:

    class MyConnectorHandler(models.AbstractModel):
        _name = 'my.connector.handler'
        _inherit = 'nats.handler'
        _nats_subjects = ['my.connector.event']

        @api.model
        def handle_nats_event(self, subject, payload):
            # business logic here
            pass

The _register_hook auto-registers the model into the NATS handler registry.
The service picks it up at startup (or immediately if already running).
"""
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class NatsHandler(models.AbstractModel):
    _name = 'nats.handler'
    _description = 'NATS Event Handler'

    # Override in subclasses: list of NATS subjects to subscribe to.
    _nats_subjects: list[str] = []

    # Champs obligatoires par sujet : {motif de sujet: {champ: type ou tuple}}.
    # Un message qui ne les a pas est mis à l'écart (« Messages en échec »)
    # au lieu d'être rejoué indéfiniment.
    _nats_schema: dict = {}

    @api.model
    def _nats_validate(self, subject: str, payload) -> str | None:
        """→ None si le message est exploitable, sinon la raison du refus."""
        if not isinstance(payload, dict):
            return 'Message non structuré (objet JSON attendu)'
        from odoo.addons.core_nats.services.nats_service import nats_subject_matches
        for pattern, required in self._nats_schema.items():
            if not nats_subject_matches(pattern, subject):
                continue
            for name, kind in required.items():
                if name not in payload or payload[name] in (None, ''):
                    return 'Champ obligatoire manquant : %s' % name
                if not isinstance(payload[name], kind):
                    return 'Champ %s de type inattendu (%s)' % (name, type(payload[name]).__name__)
        return None

    @api.model
    def _register_hook(self):
        super()._register_hook()
        if self._name == 'nats.handler':
            return
        subjects = getattr(self, '_nats_subjects', [])
        if not subjects:
            return
        from odoo.addons.core_nats.services.nats_service import register_handler_subject
        register_handler_subject(self._name, subjects)

    @api.model
    def handle_nats_event(self, subject: str, payload: dict):
        """Dispatch entry point — override in each concrete handler."""
        _logger.warning(
            f"handle_nats_event not implemented in {self._name} (subject='{subject}')"
        )
