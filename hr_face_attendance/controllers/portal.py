"""
Contrôleur portail — pointage facial employé.

GET  /my/attendance         → page portail avec aperçu caméra
POST /my/attendance/checkin → pipeline facial (liveness + verify + géofence + write)
"""

import base64
import json
import logging
import math
from datetime import datetime, timedelta

from odoo import http, fields
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)


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
        today = fields.Date.today()
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
        """Pipeline complet : liveness → verify → géofence → write attendance.

        Codes d'erreur possibles :
          not_enrolled    pas d'embedding enregistré
          no_face         aucun visage détecté dans l'image
          spoof_detected  score vivacité trop bas
          face_mismatch   l'identité ne correspond pas
          out_of_range    hors périmètre GPS
          gps_required    GPS non fourni alors qu'il est obligatoire
          service_error   erreur interne du service IA
        """
        employee = request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1
        )
        if not employee:
            return {'success': False, 'error': 'no_employee'}

        if not employee.face_portal_enabled:
            return {'success': False, 'error': 'not_authorized'}

        if not employee.face_enrolled:
            return {'success': False, 'error': 'not_enrolled'}

        # Décoder l'image
        try:
            if image_data and ',' in image_data:
                image_data = image_data.split(',', 1)[1]
            img_bytes = base64.b64decode(image_data)
        except Exception:
            return {'success': False, 'error': 'invalid_image'}

        company = employee.company_id

        # Charger le service IA
        try:
            from ..services.face_service import FaceService
            svc = FaceService.get_instance()
        except Exception as e:
            _logger.error('FaceService load error: %s', e)
            return {'success': False, 'error': 'service_error', 'details': str(e)}

        # 1. Extraction embedding
        try:
            embedding, bbox = svc.get_embedding(img_bytes)
        except Exception as e:
            return {'success': False, 'error': 'service_error', 'details': str(e)}

        if embedding is None:
            return {'success': False, 'error': 'no_face'}

        # 2. Détection de vivacité
        try:
            is_live, liveness_score = svc.check_liveness(img_bytes, bbox)
        except Exception:
            is_live, liveness_score = True, 1.0

        SPOOF_HARD_LIMIT = 0.05  # en dessous → vraie fraude, erreur affichée
        if not is_live or liveness_score < SPOOF_HARD_LIMIT:
            return {
                'success': False,
                'error': 'spoof_detected',
                'liveness_score': round(liveness_score, 4),
            }
        if liveness_score < company.face_liveness_threshold:
            # Zone grise (mauvais frame, mauvais éclairage) → réessayer silencieusement
            return {'success': False, 'error': 'retry', 'liveness_score': round(liveness_score, 4)}

        # 3. Vérification d'identité
        stored_emb = employee._decode_embedding()
        if stored_emb is None:
            return {'success': False, 'error': 'not_enrolled'}

        try:
            verified, distance = svc.verify(embedding, stored_emb, company.face_attendance_threshold)
        except Exception as e:
            return {'success': False, 'error': 'service_error', 'details': str(e)}

        confidence = round(1.0 - distance, 4)

        if not verified:
            return {
                'success': False,
                'error': 'face_mismatch',
                'confidence': confidence,
            }

        # 4. Géofencing — priorité au géofence par employé
        if employee.geofence_enabled and employee.geofence_radius:
            if not latitude or not longitude:
                return {'success': False, 'error': 'gps_required'}
            if employee.geofence_lat or employee.geofence_lng:
                dist = haversine_distance(
                    float(latitude), float(longitude),
                    employee.geofence_lat, employee.geofence_lng,
                )
                if dist > employee.geofence_radius:
                    return {
                        'success': False,
                        'error': 'out_of_range',
                        'distance': int(dist),
                        'max_distance': employee.geofence_radius,
                    }

        # 5. Enregistrement de la présence — règle premier/dernier
        # Une seule ligne par jour : premier scan = check_in, suivants = mise à jour check_out
        now = fields.Datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        Attendance = request.env['hr.attendance'].sudo()
        # limit=1 : premier check_in du jour (règle premier/dernier)
        # On ferme aussi tout record ouvert du jour précédent pour éviter les chevauchements
        open_prev = Attendance.search([
            ('employee_id', '=', employee.id),
            ('check_in', '<', today_start),
            ('check_out', '=', False),
        ])
        if open_prev:
            open_prev.write({'check_out': today_start})

        today_att = Attendance.search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', today_start),
        ], order='check_in asc', limit=1)

        vals_common = {
            'face_confidence': confidence,
            'face_liveness_score': round(liveness_score, 4),
            'check_in_latitude': float(latitude) if latitude else 0.0,
            'check_in_longitude': float(longitude) if longitude else 0.0,
        }

        try:
            if not today_att:
                # Premier pointage du jour → entrée
                vals = dict(vals_common, **{
                    'employee_id': employee.id,
                    'check_in': now,
                    'in_mode': 'face',
                })
                attendance = Attendance.create(vals)
                action_label = 'check_in'
            else:
                # Pointage existant → mise à jour de la sortie (dernier scan)
                vals = dict(vals_common, **{
                    'check_out': now,
                    'out_mode': 'face',
                })
                today_att.write(vals)
                attendance = today_att
                action_label = 'check_out'
        except Exception as e:
            _logger.error('write_error: %s', e, exc_info=True)
            return {'success': False, 'error': 'write_error', 'details': str(e)}

        # Calculer heures du jour
        hours_today = sum(
            (a.worked_hours or 0.0) for a in Attendance.search([
                ('employee_id', '=', employee.id),
                ('check_in', '>=', today_start),
            ]) if a.check_out
        )

        return {
            'success': True,
            'action': action_label,
            'employee_name': employee.name,
            'confidence': confidence,
            'liveness_score': round(liveness_score, 4),
            'hours_today': round(hours_today, 2),
        }
