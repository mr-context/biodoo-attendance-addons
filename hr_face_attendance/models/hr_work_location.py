from odoo import models, fields


class HrWorkLocation(models.Model):
    _inherit = 'hr.work.location'

    latitude = fields.Float(
        string='Latitude', digits=(10, 7),
        help='Latitude GPS du site (ex : 36.7372).',
    )
    longitude = fields.Float(
        string='Longitude', digits=(10, 7),
        help='Longitude GPS du site (ex : 3.0869).',
    )
    geofence_radius = fields.Integer(
        string='Rayon géofence (m)',
        default=200,
        help='Distance maximale autorisée depuis le centre du site pour le pointage facial.',
    )
    face_attendance_ok = fields.Boolean(
        string='Pointage facial autorisé',
        default=True,
        help='Si désactivé, le pointage facial est refusé depuis ce site.',
    )

    def action_detect_location(self):
        """Ouvre un wizard client pour détecter les coordonnées GPS depuis le navigateur."""
        return {
            'type': 'ir.actions.client',
            'tag': 'face_attendance.detect_location',
            'params': {'work_location_id': self.id},
        }

    def action_open_maps(self):
        """Ouvre Google Maps centré sur les coordonnées du site."""
        if not self.latitude and not self.longitude:
            return
        url = f"https://www.google.com/maps?q={self.latitude},{self.longitude}&z=17"
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }
