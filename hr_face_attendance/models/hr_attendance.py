from odoo import models, fields


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    check_in_latitude = fields.Float(
        string='Latitude pointage',
        readonly=True,
        digits=(10, 7),
    )
    check_in_longitude = fields.Float(
        string='Longitude pointage',
        readonly=True,
        digits=(10, 7),
    )
    face_confidence = fields.Float(
        string='Similarité faciale',
        readonly=True,
        digits=(5, 4),
        help='Score de similarité ArcFace (1 - distance cosine). Plus proche de 1 = plus sûr.',
    )
    face_liveness_score = fields.Float(
        string='Score vivacité',
        readonly=True,
        digits=(5, 4),
        help='Score MiniFASNet anti-spoofing (0 → photo/vidéo, 1 → visage réel).',
    )

    # Étendre in_mode et out_mode pour ajouter 'face'
    in_mode = fields.Selection(
        selection_add=[('face', 'Reconnaissance faciale')],
    )
    out_mode = fields.Selection(
        selection_add=[('face', 'Reconnaissance faciale')],
    )
