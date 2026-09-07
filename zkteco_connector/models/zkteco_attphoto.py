# -*- coding: utf-8 -*-
import base64
import logging
import re
from datetime import datetime, timedelta, timezone

import pytz

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# FileName from device: YYYYMMDDHHMMSS-PIN.jpg (or .jpeg)
_FNAME_RE = re.compile(r'^(\d{14})-(.+?)\.\w+$')


class ZktecoAttPhoto(models.Model):
    _name = 'zkteco.attphoto'
    _description = 'ZKTeco Attendance Photo'
    _order = 'captured_at desc'

    attendance_id = fields.Many2one(
        'hr.attendance',
        string='Pointage',
        ondelete='set null',
        index=True,
    )
    device_sn = fields.Char(string='Device S/N', readonly=True, index=True)
    employee_pin = fields.Char(string='PIN', readonly=True)
    file_name = fields.Char(string='Fichier', readonly=True)
    captured_at = fields.Datetime(string='Heure capture', readonly=True)
    file_size = fields.Integer(string='Taille (octets)', readonly=True)
    photo = fields.Binary(
        string='Photo',
        readonly=True,
        attachment=True,
    )

    @api.model
    def _create_from_nats(self, payload: dict):
        """
        Called by the TA handler when zkteco.ta.attphoto.{sn} arrives.

        Go ATTPHOTORecord JSON fields:
          SerialNumber, PIN, FileName, Size, Data (base64 JPEG bytes)
        """
        sn        = str(payload.get('SerialNumber', '')).strip()
        pin       = str(payload.get('PIN', '')).strip()
        file_name = str(payload.get('FileName', '')).strip()
        size      = int(payload.get('Size', 0) or 0)
        data_b64  = payload.get('Data')   # already base64 by json.Marshal on []byte

        if not file_name:
            _logger.warning(f"[zkteco] attphoto missing FileName from {sn}")
            return

        # Parse timestamp + PIN from filename
        captured_at = self._parse_filename_ts(file_name)
        if not captured_at:
            _logger.warning(f"[zkteco] attphoto unrecognised filename: {file_name}")

        # Avoid duplicates
        if self.sudo().search([
            ('device_sn', '=', sn),
            ('file_name', '=', file_name),
        ], limit=1):
            _logger.debug(f"[zkteco] attphoto duplicate {file_name}, skipped")
            return

        # Try to link to an existing attendance record
        attendance = self._match_attendance(pin, captured_at) if captured_at else None

        vals = {
            'device_sn':    sn,
            'employee_pin': pin,
            'file_name':    file_name,
            'captured_at':  captured_at,
            'file_size':    size,
            'attendance_id': attendance.id if attendance else False,
        }

        # Store photo data if device sent it
        if data_b64:
            if isinstance(data_b64, str):
                vals['photo'] = data_b64          # already base64 string
            elif isinstance(data_b64, (bytes, bytearray)):
                vals['photo'] = base64.b64encode(data_b64).decode()

        rec = self.sudo().create(vals)
        _logger.info(
            f"[zkteco] attphoto saved: {file_name} "
            f"({'linked' if attendance else 'unlinked'})"
        )
        return rec

    def _device_timezone(self):
        """Fuseau à utiliser pour interpréter l'heure LOCALE encodée par le device.
        Le bridge convertit déjà les pointages (ATTLOG) en UTC, mais PAS le nom de
        fichier ATTPHOTO (heure locale brute). On aligne donc la photo sur le même
        fuseau : param système `zkteco.device_timezone` s'il est posé, sinon le
        fuseau de la société, sinon celui de l'utilisateur, sinon UTC."""
        tzname = (
            self.env['ir.config_parameter'].sudo().get_param('zkteco.device_timezone')
            or self.env.company.partner_id.tz
            or self.env.user.tz
            or 'UTC'
        )
        try:
            return pytz.timezone(tzname)
        except Exception:  # noqa: BLE001 — nom de fuseau invalide → UTC sûr
            return pytz.UTC

    def _parse_filename_ts(self, file_name: str):
        """Extrait un datetime UTC (naïf) du nom de fichier device YYYYMMDDHHMMSS-PIN.ext.
        Le device encode l'heure LOCALE ; on la convertit en UTC via `_device_timezone`
        pour qu'elle s'aligne sur `hr.attendance.check_in` (stocké en UTC). Sans cette
        conversion, en UTC+1 la photo était décalée d'1 h et ne matchait jamais (I-12)."""
        m = _FNAME_RE.match(file_name)
        if not m:
            return None
        try:
            local_naive = datetime.strptime(m.group(1), '%Y%m%d%H%M%S')
        except ValueError:
            return None
        tz = self._device_timezone()
        # localize (heure locale device) → UTC → naïf pour stockage Odoo
        return tz.localize(local_naive).astimezone(pytz.UTC).replace(tzinfo=None)

    def _match_attendance(self, pin: str, captured_at: datetime):
        """Find the closest attendance record (check_in within ±2 min) for this PIN."""
        if not pin or not captured_at:
            return None
        employee = self.env['hr.employee'].sudo().search(
            [('zkteco_pin', '=', pin)], limit=1
        )
        if not employee:
            return None
        window = timedelta(minutes=2)
        att = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in',    '>=', captured_at - window),
            ('check_in',    '<=', captured_at + window),
        ], order='check_in desc', limit=1)
        return att or None
