# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

_FINGER_NAMES = {
    # Left hand (FID 0-4): 0=auriculaire → 4=pouce (spec officielle ZKTeco v5.4)
    0: 'Auriculaire G', 1: 'Annulaire G', 2: 'Majeur G',
    3: 'Index G',       4: 'Pouce G',
    # Right hand (FID 5-9): 5=pouce → 9=auriculaire
    5: 'Pouce D',       6: 'Index D',    7: 'Majeur D',
    8: 'Annulaire D',   9: 'Auriculaire D',
}


class ZktecoDeviceBiodata(models.Model):
    _name = 'zkteco.device.biodata'
    _description = 'ZKTeco Template biométrique'
    _order = 'bio_type, finger_id'

    device_user_id = fields.Many2one('zkteco.device.user', required=True,
                                     ondelete='cascade', index=True)
    employee_id    = fields.Many2one('hr.employee',
                                     related='device_user_id.employee_id', store=True)

    bio_type     = fields.Selection([
        ('1', 'Empreinte'), ('2', 'Visage NIR'), ('8', 'Paume'), ('9', 'Visage VL'),
    ], string='Type')
    finger_id    = fields.Integer(string='Index doigt', default=0)
    finger_label = fields.Char(compute='_compute_finger_label', store=True, string='Doigt')
    valid        = fields.Boolean()
    format_code  = fields.Integer(string='Format')
    major_ver    = fields.Integer(string='Version algo majeure', default=0)
    minor_ver    = fields.Integer(string='Version algo mineure', default=0)
    template     = fields.Binary(string='Template ZK', attachment=True)

    _unique_user_type_finger = models.Constraint(
        'UNIQUE(device_user_id, bio_type, finger_id)',
        'Template unique par user / type / doigt')

    @api.depends('bio_type', 'finger_id')
    def _compute_finger_label(self):
        for r in self:
            if r.bio_type == '1':
                r.finger_label = _FINGER_NAMES.get(r.finger_id, f'Doigt {r.finger_id}')
            elif r.bio_type == '8':
                r.finger_label = {0: 'Paume gauche', 1: 'Paume droite'}.get(
                    r.finger_id, 'Paume')
            else:
                r.finger_label = 'Visage'

    def action_delete_from_device(self):
        """Supprime le template du device (via commande sémantique bridge) puis le record Odoo."""
        for r in self:
            device = r.device_user_id.device_id
            pin    = r.device_user_id.pin
            if r.bio_type == '1':
                device._send_command(f'DELETE_FP PIN={pin} FID={r.finger_id}')
            else:
                # Visage stocké en BIODATA (Type=2 NIR, Type=9 visible) sur firmware moderne
                device._send_command(f'DELETE_BIODATA PIN={pin} TYPE={r.bio_type} NO={r.finger_id}')
            _logger.info(
                f"[zkteco] delete biodata: PIN={pin} type={r.bio_type} "
                f"finger={r.finger_id} sur {device.serial_number}"
            )
        self.unlink()

    @api.model
    def _upsert(self, serial_number: str, pin: str, bio_type: int,
                finger_id: int, template_b64, valid: bool, fmt: int,
                major_ver: int = 0, minor_ver: int = 0):
        DeviceUser = self.env['zkteco.device.user'].sudo()
        device_user = DeviceUser.search([
            ('device_id.serial_number', '=', serial_number),
            ('pin', '=', pin),
        ], limit=1)
        if not device_user:
            DeviceUser._upsert(serial_number, pin, '', 0, '')
            device_user = DeviceUser.search([
                ('device_id.serial_number', '=', serial_number),
                ('pin', '=', pin),
            ], limit=1)
        if not device_user:
            return

        # Normalise le template en bytes puis re-encode en base64 pour Binary
        raw = b''
        if template_b64:
            if isinstance(template_b64, (bytes, bytearray)):
                raw = bytes(template_b64)
            else:
                try:
                    raw = base64.b64decode(template_b64)
                except Exception:
                    raw = str(template_b64).encode()

        vals = {
            'valid':       valid,
            'format_code': fmt,
            'major_ver':   major_ver,
            'minor_ver':   minor_ver,
            'template':    base64.b64encode(raw).decode() if raw else False,
        }

        existing = self.sudo().search([
            ('device_user_id', '=', device_user.id),
            ('bio_type',       '=', str(bio_type)),
            ('finger_id',      '=', finger_id),
        ], limit=1)

        if existing:
            existing.write(vals)
        else:
            self.sudo().create({
                'device_user_id': device_user.id,
                'bio_type':       str(bio_type),
                'finger_id':      finger_id,
                **vals,
            })
            _logger.info(
                f"[zkteco] biodata stocké: PIN={pin} type={bio_type} "
                f"finger={finger_id} sur {serial_number}"
            )


class ZktecoDeviceBiophoto(models.Model):
    _name = 'zkteco.device.biophoto'
    _description = 'ZKTeco Photo visage (enrôlement)'

    device_user_id = fields.Many2one('zkteco.device.user', required=True,
                                     ondelete='cascade', index=True)
    employee_id    = fields.Many2one('hr.employee',
                                     related='device_user_id.employee_id', store=True)
    photo       = fields.Binary(string='Photo JPEG', attachment=True)
    filename    = fields.Char()
    captured_at = fields.Datetime(default=fields.Datetime.now)

    @api.model
    def _upsert(self, serial_number: str, pin: str, filename: str, content_b64: str):
        DeviceUser = self.env['zkteco.device.user'].sudo()
        device_user = DeviceUser.search([
            ('device_id.serial_number', '=', serial_number),
            ('pin', '=', pin),
        ], limit=1)
        if not device_user:
            DeviceUser._upsert(serial_number, pin, '', 0, '')
            device_user = DeviceUser.search([
                ('device_id.serial_number', '=', serial_number),
                ('pin', '=', pin),
            ], limit=1)
        if not device_user:
            return

        try:
            photo_bytes = base64.b64decode(content_b64) if content_b64 else b''
        except Exception:
            photo_bytes = b''

        if not photo_bytes:
            return

        photo_b64 = base64.b64encode(photo_bytes).decode()

        existing = self.sudo().search([
            ('device_user_id', '=', device_user.id),
            ('filename',       '=', filename),
        ], limit=1)

        if existing:
            existing.write({'photo': photo_b64})
        else:
            self.sudo().create({
                'device_user_id': device_user.id,
                'filename':       filename,
                'photo':          photo_b64,
            })
            _logger.info(f"[zkteco] biophoto stockée: PIN={pin} {filename} sur {serial_number}")