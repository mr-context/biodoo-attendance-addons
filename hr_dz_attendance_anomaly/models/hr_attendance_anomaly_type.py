# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class HrAttendanceAnomalyType(models.Model):
    """Type d'anomalie de présence — chaque type est une règle Python éditable
    (sur le modèle des règles de salaire Odoo), évaluée pour chaque pointage."""
    _name = 'hr.attendance.anomaly.type'
    _description = "Type d'anomalie de présence"
    _order = 'sequence, name'

    name = fields.Char(string='Nom', required=True, translate=True)
    code = fields.Char(string='Code', required=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer(string='Couleur', default=0)
    description = fields.Text(string='Description')
    severity = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Attention'),
        ('danger', 'Critique'),
    ], string='Sévérité', default='warning')
    active = fields.Boolean(default=True)

    # Dernière erreur du code de condition (affichée sur la fiche) : une règle
    # qui plante ne doit pas cesser de détecter en silence.
    last_error = fields.Text(string='Dernière erreur', readonly=True, copy=False)
    last_error_date = fields.Datetime(string='Erreur le', readonly=True, copy=False)

    def _record_error(self, message):
        self.ensure_one()
        if self.last_error != message:
            self.sudo().write({'last_error': message, 'last_error_date': fields.Datetime.now()})

    def action_clear_error(self):
        self.sudo().write({'last_error': False, 'last_error_date': False})

    # ── Condition Python (comme les règles de salaire Odoo) ─────────────────
    condition_code = fields.Text(
        string='Condition Python',
        help="""Code Python évalué pour chaque pointage.

Variables disponibles :
  check_in, check_out            → datetime (ou False)
  scheduled_start, scheduled_end → datetime (ou False)
  worked_minutes, worked_hours   → float (0 si pas de check_out)
  employee                       → hr.employee
  checkout_only                  → bool (entrée manquante)
  late_tolerance, early_tolerance → int (minutes, politique de présence de l'employé)

À assigner dans le code :
  result    = True / False   ← obligatoire
  late_min  = int            ← minutes de retard (optionnel)
  early_min = int            ← minutes de départ anticipé (optionnel)

Exemple — Retard > tolérance :
  if scheduled_start and check_in > scheduled_start:
      delta = (check_in - scheduled_start).total_seconds() / 60
      result = delta > late_tolerance
      late_min = int(delta)
""",
    )

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Le code doit être unique!',
    )

    # ── Réglage SANS CODE (mode standard) ──────────────────────────────────
    # Les types livrés se règlent par un seuil en minutes ; le code Python
    # n'est écrit qu'en mode expert (administrateur).
    detection_mode = fields.Selection([
        ('standard', 'Standard (réglage par seuil)'),
        ('expert', 'Expert (condition Python)'),
    ], string='Mode de détection', default='expert', required=True,
        help="Standard : la règle livrée, réglée par le seuil ci-dessous. "
             "Expert : condition Python libre (administrateur).")
    threshold_minutes = fields.Integer(
        string='Seuil (minutes)',
        help="Retard / départ anticipé : en plus de la tolérance de la politique "
             "de présence. Durée longue : au-delà de ce nombre de minutes. "
             "Arrivée en avance : à partir de ce nombre de minutes.")
    has_threshold = fields.Boolean(compute='_compute_has_threshold')

    _THRESHOLD_DEFAULTS = {'long_duration': 840, 'early_checkin': 30,
                           'short_duration': 1, 'late': 0, 'early_leave': 0}

    def _compute_has_threshold(self):
        for rec in self:
            rec.has_threshold = rec.code in self._THRESHOLD_DEFAULTS

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'detection_mode' not in vals and vals.get('code') in self._STANDARD_TEMPLATES:
                vals['detection_mode'] = 'standard'
        return super().create(vals_list)

    @api.model
    def _init_standard_mode(self):
        """À l'installation : les types livrés passent en mode standard."""
        self.search([('code', 'in', list(self._STANDARD_TEMPLATES))]).write(
            {'detection_mode': 'standard'})

    def _effective_condition(self):
        """Code évalué : la règle standard réglée par le seuil, ou le code expert."""
        self.ensure_one()
        template = self._STANDARD_TEMPLATES.get(self.code)
        if self.detection_mode == 'standard' and template:
            threshold = self.threshold_minutes if self.threshold_minutes or self.code in ('late', 'early_leave') \
                else self._THRESHOLD_DEFAULTS.get(self.code, 0)
            return template.replace('{threshold}', str(int(threshold)))
        return self.condition_code

    _STANDARD_TEMPLATES = {
        'no_checkout': "result = not check_out",
        'no_checkin': "result = checkout_only",
        'negative': "result = bool(check_out) and worked_minutes < 0",
        'short_duration': "result = bool(check_out) and 0 < worked_minutes < {threshold}",
        'long_duration': "result = bool(check_out) and {threshold} < worked_minutes <= 1440",
        'multi_day': "result = bool(check_out) and worked_minutes > 1440",
        'late': (
            "result = False\n"
            "if scheduled_start and check_in > scheduled_start:\n"
            "    delta = (check_in - scheduled_start).total_seconds() / 60\n"
            "    result = delta > late_tolerance + {threshold}\n"
            "    late_min = int(delta) if result else 0"
        ),
        'early_leave': (
            "result = False\n"
            "if scheduled_end and check_out and check_out < scheduled_end:\n"
            "    delta = (scheduled_end - check_out).total_seconds() / 60\n"
            "    result = delta > early_tolerance + {threshold}\n"
            "    early_min = int(delta) if result else 0"
        ),
        'early_checkin': (
            "result = False\n"
            "if scheduled_start and check_in < scheduled_start:\n"
            "    delta = (scheduled_start - check_in).total_seconds() / 60\n"
            "    result = delta >= {threshold}"
        ),
    }

    # ── Codes Python de référence par code d'anomalie ───────────────────────
    _STANDARD_CODES = {
        'no_checkout': "result = not check_out",
        'no_checkin': "result = checkout_only",
        'negative': "result = bool(check_out) and worked_minutes < 0",
        'short_duration': "result = bool(check_out) and 0 < worked_minutes < 1",
        'long_duration': "result = bool(check_out) and 840 < worked_minutes <= 1440",
        'multi_day': "result = bool(check_out) and worked_minutes > 1440",
        'overnight': (
            "is_crossday = bool(employee and employee.resource_calendar_id\n"
            "                   and getattr(employee.resource_calendar_id, 'is_crossday', False))\n"
            "result = (bool(check_out) and not is_crossday\n"
            "          and check_in.date() != check_out.date()\n"
            "          and worked_minutes <= 840)"
        ),
        'late': (
            "result = False\n"
            "if scheduled_start and check_in > scheduled_start:\n"
            "    delta = (check_in - scheduled_start).total_seconds() / 60\n"
            "    result = delta > late_tolerance\n"
            "    late_min = int(delta) if result else 0"
        ),
        'early_leave': (
            "result = False\n"
            "if scheduled_end and check_out and check_out < scheduled_end:\n"
            "    delta = (scheduled_end - check_out).total_seconds() / 60\n"
            "    result = delta > early_tolerance\n"
            "    early_min = int(delta) if result else 0"
        ),
        'early_checkin': (
            "result = False\n"
            "if scheduled_start and check_in < scheduled_start:\n"
            "    delta = (scheduled_start - check_in).total_seconds() / 60\n"
            "    result = delta >= 30"
        ),
    }

    def action_reset_conditions(self):
        """Remet les codes Python standard selon le code de chaque anomalie."""
        updated = 0
        for record in self:
            code = self._STANDARD_CODES.get(record.code)
            if code:
                record.write({'condition_code': code})
                updated += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Conditions réinitialisées'),
                'message': _('%d type(s) mis à jour avec le code standard.') % updated,
                'type': 'success',
                'sticky': False,
            }
        }