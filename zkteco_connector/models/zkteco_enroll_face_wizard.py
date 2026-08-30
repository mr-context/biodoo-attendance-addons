# -*- coding: utf-8 -*-
import base64
import logging
import numpy as np
import cv2
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ZktecoEnrollFaceWizard(models.TransientModel):
    _name = 'zkteco.enroll.face.wizard'
    _description = 'Enrôlement visage ZKTeco'

    employee_id = fields.Many2one('hr.employee', required=True, readonly=True,
                                  string='Employé')
    device_ids  = fields.Many2many(
        'zkteco.device',
        'zkteco_enroll_face_wiz_device_rel',
        'wizard_id', 'device_id',
        string='Devices cibles',
        domain=[('state', '=', 'approved')],
    )
    photo_b64   = fields.Char(string='Photo')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        emp_id = self.env.context.get('default_employee_id')
        if emp_id:
            # Pré-sélectionne les pointeuses AUTORISÉES de l'employé (chemin descendant
            # Odoo → device). L'employé y est enrôlé sous son zkteco_pin canonique même
            # si le device n'a pas encore renvoyé son OPERLOG (le sas peut être vide).
            employee = self.env['hr.employee'].browse(emp_id)
            devices = employee.zkteco_authorized_device_ids.filtered(
                lambda d: d.state == 'approved')
            res['device_ids'] = [(6, 0, devices.ids)]
        return res

    def _crop_face(self, photo_bytes):
        """Détecte le visage dans l'image et retourne un JPEG cropé centré sur lui.

        Utilise le Haar cascade frontal d'OpenCV. Si plusieurs visages sont détectés,
        prend le plus grand. Ajoute une marge de 30 % de chaque côté.
        Lève UserError si aucun visage n'est trouvé.
        """
        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise UserError("Impossible de décoder l'image capturée.")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )

        if not len(faces):
            raise UserError(
                "Aucun visage détecté dans la photo.\n"
                "Assurez-vous que le visage est bien visible et centré, puis reprenez la capture."
            )

        # Visage le plus grand si plusieurs détectés
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])

        # Marge 30 % pour ne pas couper le menton / front
        mx = int(w * 0.30)
        my = int(h * 0.30)
        h_img, w_img = img.shape[:2]
        x1 = max(0, x - mx)
        y1 = max(0, y - my)
        x2 = min(w_img, x + w + mx)
        y2 = min(h_img, y + h + my)

        cropped = img[y1:y2, x1:x2]
        _, buf = cv2.imencode('.jpg', cropped, [cv2.IMWRITE_JPEG_QUALITY, 85])
        result = buf.tobytes()
        _logger.info(
            f"[zkteco] face crop: {w_img}×{h_img} → {x2-x1}×{y2-y1} "
            f"({len(photo_bytes)//1024}KB → {len(result)//1024}KB)"
        )
        return result

    def action_send(self):
        self.ensure_one()
        if not self.photo_b64:
            raise UserError("Aucune photo capturée. Activez la caméra et prenez une photo.")
        if not self.device_ids:
            raise UserError("Sélectionnez au moins un device cible.")

        try:
            photo_bytes = base64.b64decode(self.photo_b64)
        except Exception:
            raise UserError("Format de photo invalide.")

        # PIN canonique de l'employé (identique sur toutes les pointeuses, modèle BioTime).
        pin = self.employee_id.zkteco_pin
        if not pin:
            raise UserError(
                "L'employé n'a pas de PIN ZKTeco. Renseignez son PIN avant l'enrôlement visage."
            )

        photo_bytes = self._crop_face(photo_bytes)
        photo_b64 = base64.b64encode(photo_bytes).decode()
        size = len(photo_bytes)
        errors = []

        for device in self.device_ids:
            cmd = (
                f'SYNC_BIOPHOTO PIN={pin} TYPE=9 NO=0 INDEX=0 '
                f'SIZE={size} CONTENT={photo_b64} POSTBACK=1'
            )
            try:
                # 0) mirror sas lié à l'employé IMMÉDIATEMENT (top-down : ça vient
                #    d'Odoo, on connaît déjà l'employé, pas d'attente du retour device)
                self.env['zkteco.device.user'].sudo()._upsert(
                    device.serial_number, pin, self.employee_id.name, 0, '',
                    employee=self.employee_id)
                # 1) garantit l'utilisateur sur le device (et crée le sas au pull)
                device._send_command(device._build_enroll_user_cmd(self.employee_id))
                # 2) pousse la photo ; POSTBACK=1 → le device re-génère le template
                device._send_command(cmd)
                # 3) PULL : on demande au device de re-uploader son état réel
                #    (templates générés). Le handler _process_biodata le stocke et
                #    ça remonte tout seul dans la vue device. Pas de cron, pas de bouton.
                device._send_command(f'QUERY_BIODATA PIN={pin}')
                _logger.info(
                    f"[zkteco] enroll face: PIN={pin} → {device.serial_number} "
                    f"size={size}B (+ENROLL_USER +QUERY_BIODATA)"
                )
            except Exception as e:
                errors.append(f"{device.display_name} : {e}")

        if errors:
            raise UserError(
                "Certains envois ont échoué :\n" + "\n".join(f"• {e}" for e in errors)
            )

        return {'type': 'ir.actions.act_window_close'}