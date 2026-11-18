"""Tentatives de pointage facial : chaque essai (réussi ou refusé) est tracé.

- Les RH voient les refus (fraude présumée, visage différent, hors périmètre)
  dans « À corriger » et sur la fiche de l'employé.
- Le nombre d'essais est limité : MAX_FAILURES refus en WINDOW_MINUTES bloquent
  le pointage facial pendant WINDOW_MINUTES (contre les essais en rafale avec
  une photo).
"""
from datetime import timedelta

from odoo import _, api, fields, models

MAX_FAILURES = 5
WINDOW_MINUTES = 10

RESULTS = [
    ('ok', 'Pointage enregistré'),
    ('spoof_detected', 'Fraude présumée (photo ou écran)'),
    ('liveness_unavailable', 'Contrôle anti-fraude indisponible'),
    ('face_mismatch', "Visage d'une autre personne"),
    ('no_face', 'Aucun visage détecté'),
    ('out_of_range', 'Hors du périmètre autorisé'),
    ('gps_required', 'Position GPS manquante'),
    ('site_forbidden', 'Pointage facial interdit sur ce site'),
    ('no_perimeter', 'Aucun périmètre géolocalisé défini'),
    ('too_many_attempts', "Trop d'essais : pointage bloqué"),
    ('service_error', 'Service de reconnaissance indisponible'),
    ('invalid_image', 'Image illisible'),
]


class HrFaceAttempt(models.Model):
    _name = 'hr.face.attempt'
    _description = 'Tentative de pointage facial'
    _order = 'create_date desc, id desc'

    employee_id = fields.Many2one('hr.employee', string='Employé', required=True,
                                  index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    result = fields.Selection(RESULTS, string='Résultat', required=True, index=True)
    success = fields.Boolean(compute='_compute_success', store=True)
    liveness_score = fields.Float(string='Score de vivacité', digits=(5, 4))
    confidence = fields.Float(string='Confiance faciale', digits=(5, 4))
    distance_m = fields.Integer(string='Distance au site (m)')
    perimeter = fields.Char(string='Périmètre appliqué')
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    reviewed = fields.Boolean(string='Vu par les RH')

    @api.depends('result')
    def _compute_success(self):
        for attempt in self:
            attempt.success = attempt.result == 'ok'

    @api.model
    def _is_blocked(self, employee):
        """Trop de refus récents : pointage facial bloqué un moment."""
        since = fields.Datetime.now() - timedelta(minutes=WINDOW_MINUTES)
        return self.sudo().search_count([
            ('employee_id', '=', employee.id), ('success', '=', False),
            ('result', '!=', 'too_many_attempts'), ('create_date', '>=', since),
        ]) >= MAX_FAILURES

    @api.model
    def _log(self, employee, result, **values):
        return self.sudo().create(dict(values, employee_id=employee.id, result=result))

    def action_mark_reviewed(self):
        self.write({'reviewed': True})
