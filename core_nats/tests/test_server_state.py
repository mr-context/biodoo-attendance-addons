# -*- coding: utf-8 -*-
"""Écriture de `nats.server.state` par le thread du service.

Au démarrage d'Odoo, cette écriture croise celle du démarrage automatique sur
la même ligne. Ces tests figent qu'elle prend le verrou sans attendre ni
tracer d'erreur, recommence sur une transaction neuve quand la ligne est
tenue, et n'écrit rien quand l'état est déjà le bon.

Aucune base de données : curseur et environnement sont simulés.
"""
import sys
import types
from unittest import mock

from psycopg2 import errors as pg_errors

try:  # sous Odoo
    from odoo.tests.common import BaseCase, tagged
    from odoo.addons.core_nats.services import nats_service as _mod
except ImportError:  # exécution autonome : python -m unittest tests/test_server_state.py
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


class _Recordset:
    def __init__(self, log, model):
        self._log, self._model = log, model

    def sudo(self):
        return self

    def browse(self, ids):
        self._log.append((self._model, 'browse', tuple(ids)))
        return self

    def search(self, domain):
        self._log.append((self._model, 'search', tuple(domain)))
        return self

    def write(self, vals):
        self._log.append((self._model, 'write', dict(vals)))


class _Cursor:
    """Curseur simulé : `outcomes` donne, par transaction, soit l'exception
    levée par le SELECT … FOR UPDATE NOWAIT, soit les lignes qu'il rend."""

    def __init__(self, registry):
        self._registry = registry
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, query, params=None, log_exceptions=True):
        self._registry.queries.append((query, params, log_exceptions))
        outcome = self._registry.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self._rows = outcome

    def fetchall(self):
        return self._rows

    def commit(self):
        self._registry.commits += 1


class _Registry:
    def __init__(self, outcomes):
        self.outcomes, self.queries, self.commits, self.cursors = list(outcomes), [], 0, 0

    def cursor(self):
        self.cursors += 1
        return _Cursor(self)


@tagged('post_install', '-at_install', 'core_nats')
class TestServerStateSync(BaseCase):

    def _run(self, outcomes, state='running'):
        registry = _Registry(outcomes)
        log = []
        fake_api = types.SimpleNamespace(
            Environment=lambda cr, uid, ctx: {
                'nats.server': _Recordset(log, 'nats.server'),
                'nats.subscription': _Recordset(log, 'nats.subscription'),
            })
        fake_odoo = types.ModuleType('odoo')
        fake_odoo.api, fake_odoo.SUPERUSER_ID = fake_api, 1
        service = _mod.NatsService(url='nats://127.0.0.1:1', registry=registry)
        with mock.patch.dict(sys.modules, {'odoo': fake_odoo}), \
                mock.patch.object(_mod.time, 'sleep'):
            service._db_set_server_state(state)
        return registry, log

    def test_writes_only_rows_in_another_state(self):
        registry, log = self._run([[(1,)]])
        self.assertEqual(log, [('nats.server', 'browse', (1,)),
                               ('nats.server', 'write', {'state': 'running'})])
        self.assertEqual(registry.commits, 1)

    def test_lock_is_taken_without_waiting_or_logging(self):
        registry, _log = self._run([[]])
        query, params, log_exceptions = registry.queries[0]
        self.assertIn('FOR UPDATE NOWAIT', query)
        self.assertEqual(params, ('running',))
        self.assertFalse(log_exceptions)

    def test_already_in_state_writes_nothing(self):
        registry, log = self._run([[]])
        self.assertEqual(log, [])
        self.assertEqual(registry.cursors, 1)

    def test_row_held_by_autostart_is_retried_on_a_new_transaction(self):
        # 1re transaction : la ligne est tenue par le démarrage automatique.
        # 2e : il a validé 'running', plus rien à écrire.
        registry, log = self._run([pg_errors.LockNotAvailable(), []])
        self.assertEqual(registry.cursors, 2)
        self.assertEqual(log, [])
        self.assertEqual(registry.commits, 1)

    def test_serialization_failure_is_retried(self):
        registry, log = self._run([pg_errors.SerializationFailure(), [(1,)]])
        self.assertEqual(registry.cursors, 2)
        self.assertEqual(log[-1], ('nats.server', 'write', {'state': 'running'}))

    def test_gives_up_after_bounded_tries(self):
        registry, log = self._run([pg_errors.LockNotAvailable()] * 10)
        self.assertEqual(registry.cursors, _mod._MAX_NATS_TRIES)
        self.assertEqual(log, [])

    def test_error_state_deactivates_subscriptions(self):
        _registry, log = self._run([[(1,)]], state='error')
        self.assertIn(('nats.subscription', 'write', {'state': 'inactive'}), log)
