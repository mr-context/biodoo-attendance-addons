# -*- coding: utf-8 -*-
from odoo import models, fields, _


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
  late_tolerance, early_tolerance → int (minutes, depuis la société)

À assigner dans le code :
  result    = True / False   ← obligatoire
  late_min  = int            ← minutes de retard (optionnel)
  early_min = int            ← minutes de départ anticipé (optionnel)

Exemple — Retard > tolérance :
  if scheduled_start and check_in > scheduled_start:
      delta = (check_in - scheduled_start).total_seconds() / 60
      result = delta >= late_tolerance
      late_min = int(delta)
""",
    )

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Le code doit être unique!',
    )

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
            "    result = delta >= late_tolerance\n"
            "    late_min = int(delta) if result else 0"
        ),
        'early_leave': (
            "result = False\n"
            "if scheduled_end and check_out and check_out < scheduled_end:\n"
            "    delta = (scheduled_end - check_out).total_seconds() / 60\n"
            "    result = delta >= early_tolerance\n"
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