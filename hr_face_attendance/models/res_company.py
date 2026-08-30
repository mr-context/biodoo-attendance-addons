from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    face_attendance_threshold = fields.Float(
        string='Seuil similarité faciale',
        default=0.4,
        help='Distance cosine ArcFace en dessous de laquelle l\'identité est confirmée (0.4 recommandé).',
    )
    face_liveness_threshold = fields.Float(
        string='Seuil vivacité (anti-spoofing)',
        default=0.35,
        help='Score MiniFASNet au-dessus duquel le visage est considéré vivant (0.35 recommandé). Photos/écrans < 0.01, visages réels > 0.35.',
    )
    face_geofence_enabled = fields.Boolean(
        string='Géofencing activé',
        default=True,
        help='Si activé, le pointage facial exige que l\'employé soit dans le périmètre du site.',
    )
