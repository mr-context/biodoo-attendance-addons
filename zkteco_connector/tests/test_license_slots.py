# -*- coding: utf-8 -*-
"""Une pointeuse hors ligne garde son créneau de licence.

Toutes les 15 minutes, Odoo envoie au bridge la liste des pointeuses à garder ;
le bridge libère tout créneau absent de cette liste et, sur une licence pleine,
le donne à une pointeuse en attente. Ces tests figent que :

- une pointeuse « Hors ligne » figure dans cette liste ;
- la confirmation de son créneau ne la repasse pas « Approuvé » ;
- la supprimer libère son créneau comme pour une pointeuse approuvée.

Aucune base de données : les enregistrements sont simulés.
"""
import sys
import types
from unittest import mock

try:  # sous Odoo
    from odoo.tests.common import BaseCase, tagged
    from odoo.addons.zkteco_connector.models import license_states
    from odoo.addons.zkteco_connector.models import zkteco_device_license as lic_mod
    from odoo.addons.zkteco_connector.models import zkteco_license_handler as handler_mod
except ImportError:  # exécution autonome : python -m unittest tests/test_license_slots.py
    import importlib
    import os
    import unittest

    BaseCase = unittest.TestCase

    def tagged(*_tags):
        return lambda cls: cls

    class _Model:
        def unlink(self):
            return True

    _odoo = types.ModuleType('odoo')
    _odoo.api = types.SimpleNamespace(model=lambda f: f)
    _odoo.fields = mock.MagicMock()
    _odoo.models = types.SimpleNamespace(Model=_Model, AbstractModel=_Model)

    _pkg = types.ModuleType('_zkc_models')
    _pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'models')]

    with mock.patch.dict(sys.modules, {'odoo': _odoo, '_zkc_models': _pkg}):
        license_states = importlib.import_module('_zkc_models.license_states')
        lic_mod = importlib.import_module('_zkc_models.zkteco_device_license')
        handler_mod = importlib.import_module('_zkc_models.zkteco_license_handler')


class _Device:
    """Une pointeuse simulée."""

    def __init__(self, sn, mac, state):
        self.serial_number, self.mac_address, self.state = sn, mac, state
        self.license_used = self.license_max = 0
        self.published, self.slot_requests = [], 0

    def _license_publish(self, action, payload):
        self.published.append((action, payload))
        return True

    def action_request_license_slot(self):
        self.slot_requests += 1

    def write(self, vals):
        for k, v in vals.items():
            setattr(self, k, v)

    def __bool__(self):
        return True


class _Devices(list):
    """Un jeu de pointeuses simulé (le résultat d'un search)."""

    tz_pushed = False

    def mapped(self, name):
        return [getattr(d, name) for d in self]

    def _push_device_timezone(self):
        self.tz_pushed = True


class _DeviceModel:
    """Le modèle zkteco.device simulé : applique le domaine de recherche."""

    def __init__(self, devices):
        self._all = devices
        self.synced = None

    def sudo(self):
        return self

    def search(self, domain, limit=None):
        found = _Devices()
        for d in self._all:
            ok = True
            for field, op, value in domain:
                cur = getattr(d, field)
                if op == 'in':
                    ok = ok and cur in value
                elif op == '=':
                    ok = ok and cur == value
                elif op == '!=':
                    ok = ok and (bool(cur) if value is False else cur != value)
                else:  # un opérateur non prévu doit faire échouer le test
                    raise AssertionError(f"opérateur inattendu : {op}")
            if ok:
                found.append(d)
        if limit == 1:
            return found[0] if found else None
        return found

    def _license_publish_sync(self, macs):
        self.synced = sorted(macs)
        return True

    def _cron_cache_bridge_license(self):
        return None


def _fleet():
    return [
        _Device('ONLINE01', '00:17:61:00:00:01', 'approved'),
        _Device('OFFLINE1', '00:17:61:00:00:02', 'offline'),
        _Device('HELD0001', '00:17:61:00:00:03', 'held'),
        _Device('PENDING1', '00:17:61:00:00:04', 'pending'),
        _Device('REJECTED', '00:17:61:00:00:05', 'rejected'),
        _Device('NOMAC001', False, 'approved'),
    ]


@tagged('post_install', '-at_install', 'zkteco_license')
class TestReconcileKeepsOfflineSlots(BaseCase):

    def _reconcile(self, devices):
        model = _DeviceModel(devices)
        lic_mod.ZktecoDeviceLicense._license_reconcile_all(model)
        return model

    def test_offline_device_is_in_the_sync_list(self):
        devices = _fleet()
        model = self._reconcile(devices)
        self.assertEqual(model.synced, [
            '00:17:61:00:00:01', '00:17:61:00:00:02', '00:17:61:00:00:03',
        ])

    def test_offline_device_is_reasserted(self):
        devices = _fleet()
        self._reconcile(devices)
        offline = devices[1]
        self.assertEqual(offline.published, [
            ('approve', {'mac': '00:17:61:00:00:02', 'sn': 'OFFLINE1'}),
        ])

    def test_pending_rejected_and_macless_devices_stay_out(self):
        devices = _fleet()
        model = self._reconcile(devices)
        for d in devices[3:]:
            self.assertEqual(d.published, [], d.serial_number)
            self.assertNotIn(d.mac_address, model.synced)


@tagged('post_install', '-at_install', 'zkteco_license')
class TestVerdictState(BaseCase):

    def test_rules(self):
        f = license_states.state_after_verdict
        self.assertEqual(f('pending', True), 'approved')
        self.assertEqual(f('held', True), 'approved')
        self.assertEqual(f('approved', True), 'approved')
        self.assertEqual(f('offline', True), 'offline')
        for state in ('pending', 'approved', 'held', 'offline'):
            self.assertEqual(f(state, False), 'held', state)

    def _handle(self, device, payload):
        model = _DeviceModel([device])
        handler = types.SimpleNamespace(env={'zkteco.device': model})
        handler_mod.ZktecoLicenseHandler.handle_nats_event(
            handler, 'zkteco.ta.licresult.' + device.serial_number, payload)

    def test_granted_verdict_leaves_offline_device_offline(self):
        d = _Device('OFFLINE1', '00:17:61:00:00:02', 'offline')
        self._handle(d, {'sn': 'OFFLINE1', 'mac': d.mac_address, 'granted': True,
                         'state': 'approved', 'used': 3, 'max': 5})
        self.assertEqual(d.state, 'offline')
        self.assertEqual((d.license_used, d.license_max), (3, 5))

    def test_granted_verdict_approves_pending_device(self):
        d = _Device('PENDING1', '00:17:61:00:00:04', 'pending')
        self._handle(d, {'sn': 'PENDING1', 'mac': d.mac_address, 'granted': True,
                         'state': 'approved', 'used': 1, 'max': 5})
        self.assertEqual(d.state, 'approved')

    def test_refused_verdict_parks_offline_device(self):
        d = _Device('OFFLINE1', '00:17:61:00:00:02', 'offline')
        self._handle(d, {'sn': 'OFFLINE1', 'mac': d.mac_address, 'granted': False,
                         'state': 'held', 'reason': 'no_free_slot', 'used': 5, 'max': 5})
        self.assertEqual(d.state, 'held')

    def test_release_notice_asks_for_the_slot_again(self):
        d = _Device('OFFLINE1', '00:17:61:00:00:02', 'offline')
        self._handle(d, {'sn': 'OFFLINE1', 'mac': d.mac_address, 'granted': False,
                         'state': 'released', 'reason': 'sync_orphan'})
        self.assertEqual(d.slot_requests, 1)
        self.assertEqual(d.state, 'offline')


@tagged('post_install', '-at_install', 'zkteco_license')
class TestUnlinkReleasesOfflineSlot(BaseCase):

    def test_states_owning_a_slot(self):
        self.assertEqual(set(license_states.SLOT_STATES), {'approved', 'held', 'offline'})

    def test_unlink_releases_offline_device(self):
        # Sous Odoo, dériver un modèle dans un test l'enregistrerait au registre.
        if BaseCase.__module__.startswith('odoo'):
            self.skipTest("instanciation directe d'un modèle : test autonome uniquement")
        released = []

        class _Rec(lic_mod.ZktecoDeviceLicense):
            def __init__(self, state):
                self.state = state

            def __iter__(self):
                return iter([self])

            def _license_release(self):
                released.append(self.state)

        for state in ('approved', 'held', 'offline', 'pending', 'rejected'):
            _Rec(state).unlink()
        self.assertEqual(released, ['approved', 'held', 'offline'])
