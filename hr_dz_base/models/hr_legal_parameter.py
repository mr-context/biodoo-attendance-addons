
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import date


class HrLegalParameter(models.Model):
    _name = 'hr.legal.parameter'
    _description = 'Paramètre légal'
    _order = 'code, date_from desc'

    name = fields.Char(
        string='Libellé',
        required=True,
        help='Ex: SMIG 2026, Taux CNAS Ouvrier 2025'
    )
    code = fields.Char(
        string='Code',
        required=True,
        index=True,
        help='Code technique unique (ex: smig, cnas_worker_rate)'
    )
    value = fields.Float(
        string='Valeur',
        required=True,
        digits=(16, 4),
    )
    date_from = fields.Date(
        string='Date début',
        required=True,
        default=fields.Date.today,
    )
    date_to = fields.Date(
        string='Date fin',
        help='Laisser vide si toujours en vigueur'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        help='Laisser vide pour toutes les sociétés'
    )
    notes = fields.Text(
        string='Notes',
        help='Référence légale, décret, etc.'
    )
    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    _code_date_company_uniq = models.Constraint(
        'UNIQUE(code, date_from, company_id)',
        'Un paramètre avec ce code et cette date existe déjà!',
    )

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for record in self:
            if record.date_to and record.date_from > record.date_to:
                raise ValidationError(
                    _('La date de fin doit être postérieure à la date de début.')
                )

    @api.model
    def get_value(self, code, ref_date=None, company=None):
        """
        Récupère la valeur d'un paramètre légal pour une date donnée.

        :param code: Code du paramètre (ex: 'smig', 'cnas_worker_rate')
        :param ref_date: Date de référence (défaut: aujourd'hui)
        :param company: Société (défaut: société courante)
        :return: Valeur du paramètre ou 0.0 si non trouvé
        """
        if ref_date is None:
            ref_date = date.today()
        if company is None:
            company = self.env.company

        domain = [
            ('code', '=', code),
            ('date_from', '<=', ref_date),
            ('active', '=', True),
            '|',
            ('date_to', '=', False),
            ('date_to', '>=', ref_date),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', company.id),
        ]

        # Priorité: paramètre spécifique société > paramètre global
        # NOTE: 'company_id asc nulls last' place les NULL (globaux) en dernier
        param = self.search(domain, order='company_id asc nulls last, date_from desc', limit=1)
        return param.value if param else 0.0

    @api.model
    def get_parameter(self, code, ref_date=None, company=None):
        """
        Récupère l'enregistrement complet du paramètre.
        Utile pour afficher le libellé ou les notes.
        """
        if ref_date is None:
            ref_date = date.today()
        if company is None:
            company = self.env.company

        domain = [
            ('code', '=', code),
            ('date_from', '<=', ref_date),
            ('active', '=', True),
            '|',
            ('date_to', '=', False),
            ('date_to', '>=', ref_date),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', company.id),
        ]

        return self.search(domain, order='company_id asc nulls last, date_from desc', limit=1)
