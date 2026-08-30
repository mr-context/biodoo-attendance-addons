"""
Extension du modèle hr.contract.type pour les spécificités algériennes.
"""

from odoo import models, fields


class HrContractType(models.Model):
    """Extension de hr.contract.type pour l'Algérie"""
    _inherit = 'hr.contract.type'

    # Champs spécifiques Algérie
    code = fields.Char(
        string='Code',
        help='Code court pour la codification des contrats (ex: CDI, CDD, CTA)',
    )
    is_cdd = fields.Boolean(
        string='Durée déterminée (CDD)',
        help='Si coché, la date de fin est obligatoire',
    )
    is_renewable = fields.Boolean(
        string='Renouvelable',
        default=True,
    )
    max_renewals = fields.Integer(
        string='Renouvellements max',
        default=0,
        help='0 = illimité',
    )
    max_duration_months = fields.Integer(
        string='Durée max (mois)',
        default=0,
        help='0 = illimité. Pour CDD, durée max légale.',
    )
    has_trial_period = fields.Boolean(
        string='Période d\'essai',
        default=True,
    )
    default_trial_months = fields.Integer(
        string='Essai par défaut (mois)',
        default=3,
    )
    cnas_reduction = fields.Float(
        string='Réduction CNAS (%)',
        help='Réduction cotisation CNAS (ex: CTA ANEM)',
    )
    active = fields.Boolean(
        default=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        default=lambda self: self.env.company,
    )
    description = fields.Text(
        string='Description',
    )
