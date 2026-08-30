
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class HrTrialConfirmationWizard(models.TransientModel):
    _name = 'hr.trial.confirmation.wizard'
    _description = 'Confirmation/Prolongation période d\'essai'

    version_id = fields.Many2one(
        'hr.version',
        string='Version contrat',
        required=True,
    )
    employee_id = fields.Many2one(
        related='version_id.employee_id',
        string='Employé',
    )
    trial_date_start = fields.Date(
        related='version_id.contract_date_start',
        string='Début essai',
    )
    trial_date_end = fields.Date(
        related='version_id.trial_date_end',
        string='Fin essai actuelle',
    )
    trial_state = fields.Selection(
        related='version_id.trial_state',
        string='État actuel',
    )

    action_type = fields.Selection([
        ('confirm', 'Confirmer (fin de période d\'essai)'),
        ('extend', 'Prolonger la période d\'essai'),
        ('fail', 'Non concluante (fin de contrat)'),
    ], string='Action', required=True, default='confirm')

    # Pour prolongation
    extension_months = fields.Integer(
        string='Prolongation (mois)',
        default=1,
    )
    new_trial_end = fields.Date(
        string='Nouvelle fin d\'essai',
        compute='_compute_new_trial_end',
    )

    # Pour confirmation
    confirmation_date = fields.Date(
        string='Date de confirmation',
        default=fields.Date.context_today,
    )

    # Motif
    reason = fields.Text(
        string='Motif / Observations',
    )

    @api.depends('version_id', 'extension_months', 'action_type')
    def _compute_new_trial_end(self):
        for wizard in self:
            if wizard.action_type == 'extend' and wizard.trial_date_end and wizard.extension_months:
                wizard.new_trial_end = wizard.trial_date_end + relativedelta(months=wizard.extension_months)
            else:
                wizard.new_trial_end = False

    def action_execute(self):
        """Exécuter l'action sélectionnée"""
        self.ensure_one()

        if self.action_type == 'confirm':
            self._action_confirm()
        elif self.action_type == 'extend':
            self._action_extend()
        elif self.action_type == 'fail':
            self._action_fail()

        return {'type': 'ir.actions.act_window_close'}

    def _action_confirm(self):
        """Confirmer l'employé"""
        self.version_id.confirm_trial_period()

        # Créer un message dans le chatter
        self.version_id.message_post(
            body=_('Période d\'essai confirmée le %s.<br/>%s') % (
                self.confirmation_date,
                self.reason or '',
            ),
            subject=_('Confirmation période d\'essai'),
        )

    def _action_extend(self):
        """Prolonger la période d'essai"""
        if not self.extension_months or self.extension_months <= 0:
            raise ValidationError(_('La durée de prolongation doit être positive.'))

        # Vérifier le nombre max de prolongations (généralement 1)
        if self.version_id.trial_extension_count >= 1:
            raise ValidationError(_(
                'La période d\'essai a déjà été prolongée. '
                'Une seule prolongation est autorisée.'
            ))

        self.version_id.extend_trial_period(self.extension_months)

        # Créer un avenant pour la prolongation
        self.env['hr.contract.avenant'].create({
            'version_id': self.version_id.id,
            'avenant_type': 'other',
            'date_effet': fields.Date.context_today(self),
            'reason': _('Prolongation de la période d\'essai de %s mois.\n%s') % (
                self.extension_months,
                self.reason or '',
            ),
            'state': 'applied',
        })

        self.version_id.message_post(
            body=_('Période d\'essai prolongée de %s mois. Nouvelle fin: %s<br/>%s') % (
                self.extension_months,
                self.new_trial_end,
                self.reason or '',
            ),
            subject=_('Prolongation période d\'essai'),
        )

    def _action_fail(self):
        """Période d'essai non concluante"""
        self.version_id.fail_trial_period()

        self.version_id.message_post(
            body=_('Période d\'essai non concluante.<br/>%s') % (
                self.reason or '',
            ),
            subject=_('Fin de période d\'essai - Non concluante'),
        )
