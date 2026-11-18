"""
Contrôleur portail — pointage facial employé.

GET  /my/attendance         → page portail avec aperçu caméra
POST /my/attendance/checkin → pipeline facial (liveness + verify + géofence + write)
"""

import base64
import binascii
import json
import logging
import math
from datetime import datetime, timedelta

from odoo import http, fields
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

# Score de vivacité sous lequel la fraude est certaine (photo, écran).
SPOOF_HARD_LIMIT = 0.05


def haversine_distance(lat1, lon1, lat2, lon2):
    """Distance haversine en mètres entre deux points GPS."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class FaceAttendancePortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        employee = request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1
        )
        authorized = employee.face_portal_enabled if employee else False
        values['face_portal_enabled'] = authorized
        values['face_attendance_state'] = (
            employee.attendance_state if employee and authorized else 'checked_out'
        )
        values['face_enrolled'] = employee.face_enrolled if employee and authorized else False
        return values

    # ── Page portail ─────────────────────────────────────────────────────────

    @http.route('/my/attendance', type='http', auth='user', website=True)
    def portal_attendance(self, **kw):
        employee = request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1
        )
        if not employee:
            return request.render('hr_face_attendance.portal_no_employee', {})

        if not employee.face_portal_enabled:
            return request.render('hr_face_attendance.portal_not_authorized', {})

        # Historique semaine
        today = fields.Date.context_today(request.env.user)
        week_start = today - timedelta(days=today.weekday())
        week_attendances = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', fields.Datetime.from_string(str(week_start))),
        ], order='check_in desc')

        # Site de travail avec géofence
        work_location = None
        if employee.work_location_id:
            loc = employee.work_location_id
            if loc.latitude or loc.longitude:
                work_location = loc

        company = employee.company_id
        values = {
            'employee': employee,
            'attendance_state': employee.attendance_state,
            'week_attendances': week_attendances,
            'work_location': work_location,
            'geofence_enabled': company.face_geofence_enabled,
            'page_name': 'attendance',
        }
        return request.render('hr_face_attendance.portal_attendance', values)

    # ── Check-in/out facial ──────────────────────────────────────────────────

    @http.route('/my/attendance/checkin', type='jsonrpc', auth='user', methods=['POST'])
    def face_checkin(self, image_data, latitude=None, longitude=None, **kw):
        """Pointage facial depuis le portail.

        Contrôles, tous côté serveur et dans cet ordre : autorisation, nombre
        d'essais, image, visage, vivacité (FERMÉE par défaut : si le contrôle ne
        peut pas tourner, le pointage est refusé), identité, périmètre (règle
        unique employé → site → société). Chaque essai est tracé
        (hr.face.attempt). Le pointage accepté entre dans le MÊME sas que ceux
        des pointeuses (zkteco.device.attlog, source « portail ») : mêmes règles
        d'appariement, d'anomalies et de corrections RH.

        Aucune erreur technique n'est renvoyée au navigateur : un code stable
        (voir hr_face_attempt.RESULTS) et, au besoin, distance et site.
        """
        employee = request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)
        if not employee:
            return {'success': False, 'error': 'no_employee'}
        if not employee.face_portal_enabled:
            return {'success': False, 'error': 'not_authorized'}
        if not employee.face_enrolled:
            return {'success': False, 'error': 'not_enrolled'}

        Attempt = request.env['hr.face.attempt'].sudo()
        lat = float(latitude) if latitude not in (None, '') else 0.0
        lng = float(longitude) if longitude not in (None, '') else 0.0

        def refuse(code, **extra):
            Attempt._log(employee, code, latitude=lat, longitude=lng,
                         liveness_score=extra.get('liveness_score', 0.0),
                         confidence=extra.get('confidence', 0.0),
                         distance_m=extra.get('distance', 0),
                         perimeter=extra.get('site', ''))
            return dict({'success': False, 'error': code}, **extra)

        if Attempt._is_blocked(employee):
            return refuse('too_many_attempts')

        try:
            if image_data and ',' in image_data:
                image_data = image_data.split(',', 1)[1]
            img_bytes = base64.b64decode(image_data, validate=True)
        except (ValueError, TypeError, binascii.Error):
            return refuse('invalid_image')

        company = employee.company_id
        try:
            from ..services.face_service import FaceService
            svc = FaceService.get_instance()
            embedding, bbox = svc.get_embedding(img_bytes)
        except Exception:   # biodoo: except-ok panne du service : journalisée, refus tracé, message stable
            _logger.exception('Pointage facial : service de reconnaissance en erreur (employé %s)', employee.id)
            return refuse('service_error')
        if embedding is None:
            return refuse('no_face')

        # Vivacité — fermée par défaut (voir FaceService.check_liveness).
        if not svc.liveness_available:
            return refuse('liveness_unavailable')
        is_live, liveness_score = svc.check_liveness(img_bytes, bbox)
        liveness_score = round(liveness_score, 4)
        if not is_live or liveness_score < SPOOF_HARD_LIMIT:
            return refuse('spoof_detected', liveness_score=liveness_score)
        if liveness_score < company.face_liveness_threshold:
            # Zone grise (mauvais cadrage, éclairage) : on redemande une image,
            # sans compter un refus.
            return {'success': False, 'error': 'retry', 'liveness_score': liveness_score}

        stored_emb = employee._decode_embedding()
        if stored_emb is None:
            return {'success': False, 'error': 'not_enrolled'}
        verified, distance = svc.verify(embedding, stored_emb, company.face_attendance_threshold)
        confidence = round(1.0 - distance, 4)
        if not verified:
            return refuse('face_mismatch', confidence=confidence, liveness_score=liveness_score)

        perimeter, refusal = employee._face_perimeter()
        if refusal:
            return refuse(refusal, confidence=confidence, liveness_score=liveness_score)
        if perimeter:
            if not (lat or lng):
                return refuse('gps_required', confidence=confidence, liveness_score=liveness_score,
                              site=perimeter['label'])
            dist = int(haversine_distance(lat, lng, perimeter['lat'], perimeter['lng']))
            if dist > perimeter['radius']:
                return refuse('out_of_range', confidence=confidence, liveness_score=liveness_score,
                              distance=dist, max_distance=perimeter['radius'], site=perimeter['label'])

        now = fields.Datetime.now()
        log = request.env['zkteco.device.attlog'].sudo()._stage_portal(
            employee, now, latitude=lat, longitude=lng,
            face_confidence=confidence, liveness_score=liveness_score)
        Attempt._log(employee, 'ok', latitude=lat, longitude=lng, liveness_score=liveness_score,
                     confidence=confidence, perimeter=perimeter['label'] if perimeter else '')
        attendance = log.attendance_id
        action_label = 'check_out' if attendance and attendance.check_out == now else 'check_in'

        day = request.env['zkteco.device.attlog'].sudo()._work_day_of(employee, now)
        day_logs = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id), ('check_out', '!=', False),
            ('check_in', '>=', now - timedelta(hours=36))])
        hours_today = sum(a.worked_hours for a in day_logs
                          if request.env['zkteco.device.attlog'].sudo()._work_day_of(employee, a.check_in) == day)
        return {
            'success': True,
            'action': action_label,
            'employee_name': employee.name,
            'confidence': confidence,
            'liveness_score': liveness_score,
            'hours_today': round(hours_today, 2),
        }
