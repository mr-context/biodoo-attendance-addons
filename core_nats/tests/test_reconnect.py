# -*- coding: utf-8 -*-
"""Abonnements NATS à la reconnexion : un seul abonnement vivant par sujet.

Le serveur NATS est embarqué dans le bridge : chaque redémarrage du bridge
(mise à jour, coupure de courant) fait vivre une reconnexion à Odoo. nats-py
restaure alors de lui-même les abonnements de la connexion ; ces tests figent
que le service ne se réabonne pas par-dessus (chaque message serait traité deux
fois) et qu'un sujet couvert par un flux finit toujours en JetStream.

Aucun serveur NATS ni base de données : le client est simulé, avec la seule
règle du vrai serveur qui compte ici — un consommateur durable déjà lié à un
abonnement refuse le suivant.
"""
import asyncio

try:  # sous Odoo
    from odoo.tests.common import BaseCase, tagged
    from odoo.addons.core_nats.services.nats_service import NatsService
except ImportError:  # exécution autonome : python -m unittest tests/test_reconnect.py
    import importlib.util
    import os
    import unittest

    BaseCase = unittest.TestCase

    def tagged(*_tags):
        return lambda cls: cls

    _spec = importlib.util.spec_from_file_location(
        'nats_service',
        os.path.join(os.path.dirname(__file__), '..', 'services', 'nats_service.py'),
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    NatsService = _mod.NatsService


class _FakeSub:
    def __init__(self, registry, subject, kind):
        self.subject, self.kind, self.active = subject, kind, True
        registry.append(self)

    async def unsubscribe(self, *_args):
        self.active = False


class _FakeJS:
    def __init__(self, nc):
        self._nc = nc

    async def subscribe(self, subject, cb=None, durable=None, manual_ack=False, config=None):
        if not self._nc.js_ready or subject not in self._nc.stream_subjects:
            raise RuntimeError('no stream matches subject')
        if any(s.active and s.kind == 'js' and s.subject == subject for s in self._nc.subs):
            raise RuntimeError('consumer is already bound to a subscription')
        return _FakeSub(self._nc.subs, subject, 'js')

    async def find_stream_name_by_subject(self, subject):
        if not self._nc.js_ready or subject not in self._nc.stream_subjects:
            raise LookupError(subject)
        return 'TA_EVENTS'


class _FakeNC:
    """Client NATS simulé. Les abonnements restent vivants d'une « reconnexion »
    à l'autre : c'est exactement ce que fait nats-py en les restaurant."""
    is_closed = False

    def __init__(self, stream_subjects):
        self.stream_subjects = set(stream_subjects)
        self.js_ready = True
        self.subs = []

    def jetstream(self):
        return _FakeJS(self)

    async def subscribe(self, subject, cb=None):
        return _FakeSub(self.subs, subject, 'core')

    def active(self, subject):
        return sorted(s.kind for s in self.subs if s.active and s.subject == subject)


class _NoDatabase:
    def cursor(self):
        raise RuntimeError('pas de base dans ce test')


JS_SUBJECT = 'zkteco.ta.device.>'      # couvert par un flux JetStream
CORE_SUBJECT = 'zkteco.ta.bridge.online'  # sujet core, sans flux


@tagged('post_install', '-at_install')
class TestNatsReconnect(BaseCase):

    def _service(self):
        nc = _FakeNC(stream_subjects=[JS_SUBJECT])
        svc = NatsService('nats://test', _NoDatabase())
        svc._nc, svc._js, svc._connected = nc, nc.jetstream(), True
        svc._handlers = {JS_SUBJECT: ['fake.model'], CORE_SUBJECT: ['fake.model']}
        return svc, nc

    def _run(self, *coros):
        async def _all():
            for coro in coros:
                await coro
        asyncio.run(_all())

    def _subscribe_all(self, svc):
        return [svc._subscribe(s) for s in list(svc._handlers)]

    def test_reconnect_keeps_one_subscription_per_subject(self):
        svc, nc = self._service()
        self._run(*self._subscribe_all(svc))
        self.assertEqual(nc.active(JS_SUBJECT), ['js'])
        self.assertEqual(nc.active(CORE_SUBJECT), ['core'])

        for _ in range(3):  # trois redémarrages du bridge d'affilée
            self._run(svc._on_reconnected())

        self.assertEqual(nc.active(JS_SUBJECT), ['js'],
                         "le sujet JetStream doit rester en JetStream, une seule fois")
        self.assertEqual(nc.active(CORE_SUBJECT), ['core'],
                         "le sujet core ne doit pas être abonné une seconde fois")
        self.assertEqual(svc._subscribed_as_js, {JS_SUBJECT})

    def test_jetstream_not_ready_falls_back_then_upgrades(self):
        svc, nc = self._service()
        self._run(*self._subscribe_all(svc))

        nc.js_ready = False  # serveur tout juste relancé : flux pas encore chargés
        self._run(svc._on_reconnected())
        self.assertEqual(nc.active(JS_SUBJECT), ['core'],
                         "sans JetStream, repli en core — une seule fois")
        self.assertEqual(svc._subscribed_as_js, set())

        self._run(svc._upgrade_core_subscriptions())  # toujours pas prêt
        self.assertEqual(nc.active(JS_SUBJECT), ['core'],
                         "un rattrapage qui échoue laisse l'abonnement core intact")

        nc.js_ready = True
        self._run(svc._upgrade_core_subscriptions())
        self.assertEqual(nc.active(JS_SUBJECT), ['js'],
                         "dès que le flux existe, le sujet repasse en JetStream")
        self.assertEqual(svc._subscribed_as_js, {JS_SUBJECT})

    def test_upgrade_leaves_core_only_subjects_alone(self):
        svc, nc = self._service()
        self._run(*self._subscribe_all(svc))
        core_sub = svc._subs[CORE_SUBJECT]

        self._run(svc._upgrade_core_subscriptions())

        self.assertIs(svc._subs[CORE_SUBJECT], core_sub)
        self.assertEqual(nc.active(CORE_SUBJECT), ['core'])

    def test_replay_does_not_leave_the_old_subscription(self):
        svc, nc = self._service()
        self._run(*self._subscribe_all(svc))

        self._run(svc._unsubscribe(JS_SUBJECT), svc._subscribe(JS_SUBJECT))

        self.assertEqual(nc.active(JS_SUBJECT), ['js'])
