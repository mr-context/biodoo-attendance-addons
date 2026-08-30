"""
Wizard d'enrollment facial.

États : idle → capturing → done
Le RH ouvre ce wizard depuis la fiche employé.
Le JS OWL gère la caméra et envoie les frames au contrôleur /hr/face/enroll.
Quand les 3 angles (face, left, right) sont capturés, l'embedding moyen est stocké.
"""

import base64
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FaceEnrollmentWizard(models.TransientModel):
    _name = 'face.enrollment.wizard'
    _description = 'Wizard d\'enrollment facial'

    employee_id = fields.Many2one(
        'hr.employee',
        string='Employé',
        required=True,
        default=lambda self: self.env.context.get('default_employee_id'),
    )
    state = fields.Selection([
        ('idle', 'En attente'),
        ('capturing', 'Capture en cours'),
        ('done', 'Terminé'),
    ], default='idle', string='État')

    # Embeddings temporaires par angle (base64 float32)
    emb_front = fields.Binary(string='Embedding face', attachment=False)
    emb_left = fields.Binary(string='Embedding gauche', attachment=False)
    emb_right = fields.Binary(string='Embedding droite', attachment=False)

    enrolled_date = fields.Datetime(
        string='Enregistré le',
        readonly=True,
    )
    face_enrolled = fields.Boolean(
        related='employee_id.face_enrolled',
        string='Déjà enregistré',
    )

    @api.depends('emb_front', 'emb_left', 'emb_right')
    def _compute_progress(self):
        for wiz in self:
            count = sum([bool(wiz.emb_front), bool(wiz.emb_left), bool(wiz.emb_right)])
            wiz.capture_progress = count

    capture_progress = fields.Integer(
        string='Angles capturés',
        compute='_compute_progress',
    )

    def action_start_capture(self):
        """Passe en mode capturing — le JS prend le relais."""
        self.ensure_one()
        self.write({
            'state': 'capturing',
            'emb_front': False,
            'emb_left': False,
            'emb_right': False,
        })
        return self._reload()

    def action_store_embedding(self, angle, embedding_b64):
        """Appelé par le contrôleur JS pour stocker l'embedding d'un angle.

        angle : 'front' | 'left' | 'right'
        embedding_b64 : bytes base64 d'un vecteur float32 512d
        """
        self.ensure_one()
        field_map = {'front': 'emb_front', 'left': 'emb_left', 'right': 'emb_right'}
        field = field_map.get(angle)
        if not field:
            raise UserError(_('Angle inconnu : %s') % angle)
        self.write({field: embedding_b64})

        # Si les 3 angles sont capturés → finaliser
        self._check_and_finalize()

    def _check_and_finalize(self):
        """Si les 3 embeddings sont présents, calcule la moyenne et stocke sur l'employé."""
        if not (self.emb_front and self.emb_left and self.emb_right):
            return

        import numpy as np

        embeddings = []
        for b64_val in [self.emb_front, self.emb_left, self.emb_right]:
            raw = base64.b64decode(b64_val)
            embeddings.append(np.frombuffer(raw, dtype=np.float32))

        mean_emb = np.mean(embeddings, axis=0).astype(np.float32)
        mean_b64 = base64.b64encode(mean_emb.tobytes())

        now = fields.Datetime.now()
        self.employee_id.write({
            'face_embedding': mean_b64,
            'face_enrolled_date': now,
            'face_enrolled_by': self.env.user.id,
        })
        self.write({
            'state': 'done',
            'enrolled_date': now,
        })

    def action_reset(self):
        """Recommence la capture depuis zéro."""
        self.ensure_one()
        self.write({
            'state': 'idle',
            'emb_front': False,
            'emb_left': False,
            'emb_right': False,
        })
        return self._reload()

    def action_delete_enrollment(self):
        """Supprime l'enrollment de l'employé."""
        self.ensure_one()
        self.employee_id.action_clear_face_enrollment()
        self.write({'state': 'idle'})
        return self._reload()

    def _reload(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'face.enrollment.wizard',
            'res_id': self.id,
            'views': [[False, 'form']],
            'target': 'new',
        }
