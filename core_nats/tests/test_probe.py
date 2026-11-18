# -*- coding: utf-8 -*-
"""Sondes du contrôle de licence : exécutées en lecture seule, signées, uniques."""
import base64
import json
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo.sql_db import db_connect
from odoo.tests.common import TransactionCase, tagged
from odoo.addons.core_nats.services import probe


@tagged('post_install', '-at_install', 'core_nats', 'dz_probe')
class TestProbe(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = Ed25519PrivateKey.generate()
        cls.pub = base64.b64encode(cls.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        cls._reset_pin()

    @classmethod
    def tearDownClass(cls):
        cls._reset_pin()
        super().tearDownClass()

    @classmethod
    def _reset_pin(cls):
        with db_connect(cls.env.registry.db_name).cursor() as cr:
            cr.execute("DELETE FROM ir_config_parameter WHERE key = %s", (probe.PIN_KEY,))

    def _req(self, sql, key=None, ts=None, nonce=None):
        key = key or self.key
        pub = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        nonce, ts = nonce or uuid.uuid4().hex, ts or int(time.time())
        sig = base64.b64encode(key.sign(probe._signed_bytes(nonce, ts, sql))).decode()
        return json.dumps({'v': 1, 'nonce': nonce, 'ts': ts, 'sql': sql, 'pub': pub, 'sig': sig}).encode()

    def _ask(self, sql, **kw):
        return probe.answer(self.env.registry, self._req(sql, **kw))

    def test_generic_execution_and_read_only(self):
        r = self._ask("select 41 + 1")
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['rows'][0][0], 42)
        r = self._ask("select (to_regclass('public.res_users') is not null)::int")
        self.assertEqual(r['rows'][0][0], 1)
        # Lecture seule : une écriture est refusée par PostgreSQL.
        r = self._ask("update res_users set active = active where id = -1")
        self.assertFalse(r['ok'])
        r = self._ask("select 1; select 2")
        self.assertFalse(r['ok'], "une seule instruction")

    def test_signature_replay_and_pinned_key(self):
        self._ask("select 1")  # épingle la clé de test
        other = Ed25519PrivateKey.generate()
        self.assertIn('clé inconnue', self._ask("select 1", key=other)['error'])
        data = json.loads(self._req("select 1"))
        data['sql'] = "select 2"   # contenu modifié après signature
        self.assertEqual(probe.answer(self.env.registry, json.dumps(data).encode())['error'], 'signature invalide')
        nonce = uuid.uuid4().hex
        self.assertTrue(self._ask("select 1", nonce=nonce)['ok'])
        self.assertIn('déjà jouée', self._ask("select 1", nonce=nonce)['error'])
        self.assertIn('hors délai', self._ask("select 1", ts=int(time.time()) - 3600)['error'])
