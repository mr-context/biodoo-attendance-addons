
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class HrContractRenewalWizard(models.TransientModel):
    _name = 'hr.contract.renewal.wizard'
    _description = 'Renouvellement de contrat CDD'

    version_id = fields.Many2one(
        'hr.version',
        string='Version actuelle',
        required=True,
    )
    employee_id = fields.Many2one(
        related='version_id.employee_id',
        string='Employé',
    )
    current_date_end = fields.Date(
        related='version_id.contract_date_end',
        string='Fin contrat actuel',
    )
    current_renewal_count = fields.Integer(
        related='version_id.renewal_count',
        string='Renouvellements effectués',
    )

    # Nouveau contrat
    new_date_start = fields.Date(
        string='Début nouveau contrat',
        required=True,
    )
    new_duration_months = fields.Integer(
        string='Durée (mois)',
        required=True,
        default=12,
    )
    new_date_end = fields.Date(
        string='Fin nouveau contrat',
        compute='_compute_new_date_end',
    )
    new_wage = fields.Monetary(
        string='Nouveau salaire',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='version_id.currency_id',
    )
    keep_same_wage = fields.Boolean(
        string='Garder le même salaire',
        default=True,
    )

    # Options
    with_trial_period = fields.Boolean(
        string='Avec période d\'essai',
        default=False,
        help='Généralement non pour un renouvellement',
    )
    close_current_contract = fields.Boolean(
        string='Clôturer le contrat actuel',
        default=True,
        help='Définit la date de fin du contrat actuel',
    )

    reason = fields.Text(
        string='Motif du renouvellement',
    )

    @api.onchange('version_id')
    def _onchange_version_id(self):
        if self.version_id:
            # Date de début = lendemain de la fin du contrat actuel
            if self.version_id.contract_date_end:
                self.new_date_start = self.version_id.contract_date_end + relativedelta(days=1)
            self.new_wage = self.version_id.wage

    @api.depends('new_date_start', 'new_duration_months')
    def _compute_new_date_end(self):
        for wizard in self:
            if wizard.new_date_start and wizard.new_duration_months:
                wizard.new_date_end = wizard.new_date_start + relativedelta(
                    months=wizard.new_duration_months
                ) - relativedelta(days=1)
            else:
                wizard.new_date_end = False

    @api.onchange('keep_same_wage', 'version_id')
    def _onchange_keep_same_wage(self):
        if self.keep_same_wage and self.version_id:
            self.new_wage = self.version_id.wage

    def action_renew(self):
        """Créer le nouveau contrat de renouvellement"""
        self.ensure_one()

        version = self.version_id
        contract_type = version.contract_type_id

        # Vérifier les limites de renouvellement
        if contract_type and contract_type.max_renewals > 0:
            total_renewals = version.renewal_count + 1
            if total_renewals > contract_type.max_renewals:
                raise ValidationError(_(
                    'Nombre maximum de renouvellements atteint (%s).'
                ) % contract_type.max_renewals)

        # Vérifier la durée max
        if contract_type and contract_type.max_duration_months > 0:
            # Calculer la durée totale approximative
            total_months = self.new_duration_months + (version.duration_months or 12)
            if total_months > contract_type.max_duration_months:
                raise ValidationError(_(
                    'La durée totale des contrats (%s mois) dépasse la limite légale (%s mois).'
                ) % (total_months, contract_type.max_duration_months))

        # Déterminer le contrat parent
        parent_version = version.parent_version_id or version

        # Créer la nouvelle version
        trial_date_end = False
        if self.with_trial_period and contract_type and contract_type.has_trial_period:
            trial_date_end = self.new_date_start + relativedelta(
                months=contract_type.default_trial_months
            ) - relativedelta(days=1)

        new_version_vals = {
            'name': _('%s (Renouvellement %s)') % (
                version.employee_id.name,
                version.renewal_count + 1
            ),
            'employee_id': version.employee_id.id,
            'contract_type_id': version.contract_type_id.id if version.contract_type_id else False,
            'job_id': version.job_id.id if version.job_id else False,
            'department_id': version.department_id.id if version.department_id else False,
            'date_version': self.new_date_start,
            'contract_date_start': self.new_date_start,
            'contract_date_end': self.new_date_end,
            'duration_months': self.new_duration_months,
            'wage': self.new_wage if not self.keep_same_wage else version.wage,
            'resource_calendar_id': version.resource_calendar_id.id if version.resource_calendar_id else False,
            'parent_version_id': parent_version.id,
            'renewal_count': version.renewal_count + 1,
            'trial_date_end': trial_date_end,
            'trial_duration_months': contract_type.default_trial_months if contract_type and self.with_trial_period else 0,
        }

        # Copier les champs spécifiques DZ si présents
        if hasattr(version, 'work_schedule_type'):
            new_version_vals['work_schedule_type'] = version.work_schedule_type
        if hasattr(version, 'is_overnight_shift'):
            new_version_vals['is_overnight_shift'] = version.is_overnight_shift

        new_version = self.env['hr.version'].create(new_version_vals)

        # Message dans le chatter de l'ancienne version
        version.message_post(
            body=_('Contrat renouvelé. Nouvelle version créée.<br/>%s') % (
                self.reason or '',
            ),
            subject=_('Renouvellement de contrat'),
        )

        # Ouvrir la fiche employé avec la nouvelle version
        return {
            'type': 'ir.actions.act_window',
            'name': _('Employé'),
            'res_model': 'hr.employee',
            'res_id': version.employee_id.id,
            'view_mode': 'form',
            'context': {'version_id': new_version.id},
        }
