"""
Contrôleur enrollment facial — appelé par le JS OWL du wizard.
Seuls les membres du groupe hr.group_hr_manager peuvent accéder à cet endpoint.
"""

import base64
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class FaceEnrollmentController(http.Controller):

    @http.route('/hr/face/enroll', type='jsonrpc', auth='user', methods=['POST'])
    def enroll_face(self, wizard_id, employee_id, image_data, angle, **kw):
        """Reçoit une frame base64 + angle et extrait l'embedding.

        Paramètres JSON :
          wizard_id   : id du wizard TransientModel en cours
          employee_id : id de l'employé
          image_data  : image JPEG encodée en base64 (data URL ou raw)
          angle       : 'front' | 'left' | 'right'

        Retourne :
          {'success': True, 'captured': [angles capturés], 'done': bool}
          ou {'success': False, 'error': str}
        """
        # Vérification droits RH
        if not request.env.user.has_group('hr.group_hr_manager'):
            return {'success': False, 'error': 'not_authorized'}

        if angle not in ('front', 'left', 'right'):
            return {'success': False, 'error': 'invalid_angle'}

        # Décoder l'image (data URL ou base64 brut)
        try:
            if ',' in image_data:
                image_data = image_data.split(',', 1)[1]
            img_bytes = base64.b64decode(image_data)
        except Exception:
            return {'success': False, 'error': 'invalid_image'}

        # Extraire l'embedding via FaceService
        try:
            from ..services.face_service import FaceService
            svc = FaceService.get_instance()
            embedding, bbox = svc.get_embedding(img_bytes)
        except Exception as e:
            _logger.error('FaceService error during enrollment: %s', e)
            return {'success': False, 'error': 'service_error', 'details': str(e)}

        if embedding is None:
            return {'success': False, 'error': 'no_face'}

        # Encoder l'embedding en base64
        emb_b64 = base64.b64encode(embedding.astype('float32').tobytes()).decode()

        # Stocker dans le wizard
        try:
            wizard = request.env['face.enrollment.wizard'].browse(int(wizard_id))
            wizard.action_store_embedding(angle, emb_b64)
        except Exception as e:
            _logger.error('Wizard store error: %s', e)
            return {'success': False, 'error': 'store_error'}

        # Recharger le wizard pour connaître l'avancement
        wizard.invalidate_recordset()
        captured = []
        if wizard.emb_front:
            captured.append('front')
        if wizard.emb_left:
            captured.append('left')
        if wizard.emb_right:
            captured.append('right')

        return {
            'success': True,
            'angle': angle,
            'captured': captured,
            'done': wizard.state == 'done',
        }
