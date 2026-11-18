from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.constrains('face_attendance_threshold', 'face_liveness_threshold')
    def _check_face_thresholds(self):
        """Seuils bornés : un seuil de similarité trop large accepte n'importe
        quel visage, un seuil de vivacité à 0 désactive l'anti-fraude."""
        for company in self:
            if not 0.2 <= company.face_attendance_threshold <= 0.6:
                raise ValidationError(_("Le seuil de similarité faciale doit être entre 0,2 et 0,6 "
                                        "(0,4 recommandé)."))
            if not 0.2 <= company.face_liveness_threshold <= 0.9:
                raise ValidationError(_("Le seuil de vivacité doit être entre 0,2 et 0,9 "
                                        "(0,35 recommandé)."))

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
