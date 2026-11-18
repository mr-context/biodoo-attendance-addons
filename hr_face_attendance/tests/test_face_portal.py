"""Pointage facial depuis le portail, avec un service de reconnaissance simulé.

Vérifie les garanties côté serveur : vivacité fermée par défaut, périmètre
unique (employé → site → société), nombre d'essais limité et tracé, passage
par le sas des pointeuses, position d'entrée conservée à la sortie.
"""
import base64
from unittest.mock import patch

from freezegun import freeze_time

import numpy as np

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.hr_face_attendance.services import face_service

IMAGE = 'data:image/jpeg;base64,' + base64.b64encode(b'fausse-image').decode()
ALGER = (36.7538, 3.0588)


class FakeFaceService:
    """Doublure du service : chaque test règle ce qu'il renvoie."""
    liveness_available = True
    live = (True, 0.9)
    verified = (True, 0.1)

    def get_embedding(self, img_bytes):
        return np.ones(512, dtype=np.float32), (0, 0, 10, 10)

    def check_liveness(self, img_bytes, bbox):
        return self.live

    def verify(self, a, b, threshold):
        return self.verified


@tagged('post_install', '-at_install', 'dz_face')
class TestFacePortal(HttpCase):

    def setUp(self):
        super().setUp()
        self.fake = FakeFaceService()
        patcher = patch.object(face_service.FaceService, 'get_instance', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = new_test_user(self.env, login='portail_visage', groups='base.group_portal',
                                  password='portail_visage')
        self.site = self.env['hr.work.location'].create({
            'name': 'Siège Alger', 'location_type': 'office',
            'address_id': self.env.company.partner_id.id,
            'latitude': ALGER[0], 'longitude': ALGER[1], 'geofence_radius': 200})
        self.employee = self.env['hr.employee'].create({
            'name': 'Employé visage', 'user_id': self.user.id, 'tz': 'Africa/Algiers',
            'work_location_id': self.site.id})
        self.employee.sudo().write({
            'face_embedding': base64.b64encode(np.ones(512, dtype=np.float32).tobytes()),
            'face_portal_enabled': True})
        self.env.company.face_geofence_enabled = True
        self.authenticate('portail_visage', 'portail_visage')

    def _punch(self, lat=ALGER[0], lng=ALGER[1]):
        return self.make_jsonrpc_request('/my/attendance/checkin',
                                         {'image_data': IMAGE, 'latitude': lat, 'longitude': lng})

    def _attempts(self, result=None):
        domain = [('employee_id', '=', self.employee.id)]
        if result:
            domain.append(('result', '=', result))
        return self.env['hr.face.attempt'].search(domain)

    def test_liveness_unavailable_refuses(self):
        """Fermé par défaut : sans contrôle anti-fraude, pas de pointage."""
        self.fake.liveness_available = False
        result = self._punch()
        self.assertEqual(result['error'], 'liveness_unavailable')
        self.assertNotIn('details', result, "aucune erreur technique vers le navigateur")
        self.assertFalse(self.env['hr.attendance'].search([('employee_id', '=', self.employee.id)]))
        self.assertTrue(self._attempts('liveness_unavailable'))

    def test_photo_refused_and_traced(self):
        self.fake.live = (True, 0.01)
        self.assertEqual(self._punch()['error'], 'spoof_detected')
        self.assertTrue(self._attempts('spoof_detected'))

    def test_site_perimeter_applied_by_server(self):
        """Règle du site appliquée côté serveur (le navigateur n'est pas cru)."""
        result = self._punch(36.80, 3.10)   # ~6 km
        self.assertEqual(result['error'], 'out_of_range')
        self.assertEqual(result['site'], 'Siège Alger')
        self.assertGreater(result['distance'], 1000)

    def test_zero_center_is_not_a_perimeter(self):
        self.site.write({'latitude': 0.0, 'longitude': 0.0})
        self.assertEqual(self._punch()['error'], 'no_perimeter')

    def test_site_forbids_face_punch(self):
        self.site.face_attendance_ok = False
        self.assertEqual(self._punch()['error'], 'site_forbidden')

    def test_employee_perimeter_has_priority(self):
        self.employee.sudo().write({'geofence_enabled': True, 'geofence_lat': 36.80,
                                    'geofence_lng': 3.10, 'geofence_radius': 300})
        self.assertTrue(self._punch(36.80, 3.10)['success'])

    def test_attempts_are_limited(self):
        self.fake.verified = (False, 0.9)
        for _i in range(5):
            self.assertEqual(self._punch()['error'], 'face_mismatch')
        self.fake.verified = (True, 0.1)
        self.assertEqual(self._punch()['error'], 'too_many_attempts')

    def test_punch_goes_through_sas_and_keeps_entry_gps(self):
        with freeze_time('2026-10-05 07:00:00'):          # 8 h à Alger
            self.assertTrue(self._punch(ALGER[0], ALGER[1])['success'])
        log = self.env['zkteco.device.attlog'].search([('employee_id', '=', self.employee.id)])
        self.assertEqual(log.source, 'portal')
        self.assertFalse(log.device_id)
        self.assertAlmostEqual(log.attendance_id.in_latitude, ALGER[0], places=4)
        # Sortie à 12 h, ailleurs dans le périmètre : l'entrée n'est pas écrasée.
        with freeze_time('2026-10-05 11:00:00'):
            self.assertTrue(self._punch(36.7545, 3.0590)['success'])
        att = self.env['hr.attendance'].search([('employee_id', '=', self.employee.id)])
        self.assertEqual(len(att), 1)
        self.assertTrue(att.check_out)
        self.assertAlmostEqual(att.in_latitude, ALGER[0], places=4)
        self.assertAlmostEqual(att.out_latitude, 36.7545, places=4)


@tagged('post_install', '-at_install', 'dz_face')
class TestFaceSettings(HttpCase):

    def test_thresholds_are_bounded(self):
        with self.assertRaises(ValidationError):
            self.env.company.face_attendance_threshold = 0.95
        with self.assertRaises(ValidationError):
            self.env.company.face_liveness_threshold = 0.0

    def test_cannot_enable_without_antifraud_model(self):
        fake = FakeFaceService()
        fake.liveness_available = False
        employee = self.env['hr.employee'].create({'name': 'Sans anti-fraude'})
        with patch.object(face_service.FaceService, 'get_instance', return_value=fake):
            with self.assertRaises(ValidationError):
                employee.face_portal_enabled = True
