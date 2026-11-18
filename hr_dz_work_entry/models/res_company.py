from odoo import api, models, fields, _


class ResCompany(models.Model):
    _inherit = 'res.company'

    attendance_policy_id = fields.Many2one(
        'hr.attendance.policy', string='Politique de présence par défaut',
        domain="[('company_id', '=', id)]",
        help="Règles d'absence, de retard, de départ anticipé et de pointage manquant "
             "pour les employés de la société qui n'ont pas de politique propre.")

    # Anciens réglages (avant les politiques de présence), conservés pour
    # reprendre leurs valeurs dans la politique par défaut créée à la mise à jour.
    late_sanction_enabled = fields.Boolean(string='Activer la sanction retard', default=False)
    late_sanction_mode = fields.Selection([
        ('minutes', 'Minutes exactes de retard'),
        ('half_day', 'Demi-journée'),
        ('full_day', 'Journée entière'),
    ], string='Mode de déduction', default='minutes')

    def _dz_legacy_policy_values(self):
        """Valeurs de la politique par défaut, reprises des anciens réglages
        (les tolérances venaient de hr_dz_attendance_anomaly)."""
        self.ensure_one()
        sanction = self.late_sanction_enabled
        return {
            'name': _('Politique standard'),
            'company_id': self.id,
            'late_tolerance_minutes': getattr(self, 'late_tolerance_minutes', 5) or 5,
            'early_tolerance_minutes': getattr(self, 'early_tolerance_minutes', 5) or 5,
            'late_rule': 'per_occurrence',
            'late_cost': (self.late_sanction_mode or 'minutes') if sanction else 'minutes',
            # Avant : les retenues n'étaient validées automatiquement (pipeline
            # paie) que si la sanction était activée.
            'mode': 'auto' if sanction else 'manual',
        }

    def _dz_ensure_attendance_policy(self):
        """Crée la politique par défaut des sociétés qui n'en ont pas."""
        Policy = self.env['hr.attendance.policy'].sudo()
        for company in self.sudo().filtered(lambda c: not c.attendance_policy_id):
            company.attendance_policy_id = Policy.create(company._dz_legacy_policy_values())

    def _dz_attendance_policy(self):
        """Politique par défaut ; à défaut, une politique virtuelle aux valeurs
        par défaut — jamais d'écriture pendant un calcul."""
        self.ensure_one()
        return self.attendance_policy_id or self.env['hr.attendance.policy'].new(
            self._dz_legacy_policy_values())

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        companies._dz_ensure_attendance_policy()
        return companies
