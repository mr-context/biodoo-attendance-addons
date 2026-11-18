# -*- coding: utf-8 -*-
"""Messages en échec : relivraison avec délai croissant, puis mise à l'écart.

Un message qui échoue n'est jamais acquitté en silence ni rejoué sans fin :
- échec passager → relivré plus tard (délai qui double, plafonné) ;
- échec après MAX_DELIVER essais, ou message malformé → rangé dans
  « Messages en échec » puis acquitté (il ne bloque plus les suivants).
"""
import asyncio
import json
from types import SimpleNamespace

from odoo.tests.common import TransactionCase, tagged
from odoo.addons.core_nats.services import nats_service
from odoo.addons.core_nats.services.nats_service import NatsService


class _Msg:
    def __init__(self, data, delivered=1, subject='zkteco.ta.attendance.SN1'):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.subject = subject
        self.metadata = SimpleNamespace(num_delivered=delivered)
        self.acked = False
        self.nak_delay = None

    async def ack(self):
        self.acked = True

    async def nak(self, delay=None):
        self.nak_delay = delay


@tagged('post_install', '-at_install', 'core_nats')
class TestDeadLetter(TransactionCase):

    def _service(self, result):
        svc = NatsService('nats://test', None)
        svc.dead = []
        svc._dispatch = lambda subject, payload, pattern: result
        svc._dead_letter = lambda subject, data, reason, n: svc.dead.append((reason, n))
        return svc

    def _handle(self, svc, msg):
        asyncio.run(svc._handle_js_msg('zkteco.ta.attendance.>', msg))

    def test_success_is_acked(self):
        svc, msg = self._service((True, None)), _Msg({'a': 1})
        self._handle(svc, msg)
        self.assertTrue(msg.acked)
        self.assertFalse(svc.dead)

    def test_failure_is_redelivered_later_with_growing_delay(self):
        svc, msg = self._service((False, None)), _Msg({'a': 1}, delivered=3)
        self._handle(svc, msg)
        self.assertFalse(msg.acked)
        self.assertEqual(msg.nak_delay, 8)

    def test_too_many_failures_go_to_dead_letters(self):
        svc = self._service((False, None))
        msg = _Msg({'a': 1}, delivered=nats_service.MAX_DELIVER)
        self._handle(svc, msg)
        self.assertTrue(msg.acked, "acquitté pour ne plus bloquer les suivants")
        self.assertEqual(len(svc.dead), 1)

    def test_malformed_message_is_set_aside_at_once(self):
        svc, msg = self._service((True, None)), _Msg(b'{pas du json', delivered=1)
        self._handle(svc, msg)
        self.assertTrue(msg.acked)
        self.assertIn('illisible', svc.dead[0][0])

    def test_schema_refusal_is_poison(self):
        svc = self._service((False, 'Champ obligatoire manquant : UserID'))
        msg = _Msg({'SerialNumber': 'SN1'}, delivered=1)
        self._handle(svc, msg)
        self.assertTrue(msg.acked)
        self.assertIn('UserID', svc.dead[0][0])

    def test_handler_schema_validation(self):
        handler = self.env['zkteco.ta.handler'] if 'zkteco.ta.handler' in self.env else None
        if handler is None:
            self.skipTest('connecteur ZKTeco non installé')
        self.assertTrue(handler._nats_validate('zkteco.ta.attendance.SN1', {'SerialNumber': 'SN1'}))
        self.assertIsNone(handler._nats_validate('zkteco.ta.attendance.SN1', {
            'SerialNumber': 'SN1', 'UserID': '12', 'Timestamp': '2026-10-05T08:00:00+01:00'}))

    def test_dead_letter_replay(self):
        letter = self.env['nats.dead.letter'].create({
            'subject': 'zkteco.ta.unknown.SN1', 'payload': '{"a": 1}', 'reason': 'test'})
        letter.action_replay()
        self.assertEqual(letter.state, 'new')
        self.assertTrue(letter.last_error)
