
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class HrContractAvenant(models.Model):
    _name = 'hr.contract.avenant'
    _description = 'Avenant au contrat'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_effet desc, id desc'

    name = fields.Char(
        string='Référence',
        readonly=True,
        copy=False,
        index=True,
    )
    version_id = fields.Many2one(
        'hr.version',
        string='Version contrat',
        required=True,
        ondelete='cascade',
    )
    # Alias pour compatibilité
    contract_id = fields.Many2one(
        related='version_id',
        string='Contrat',
    )
    employee_id = fields.Many2one(
        related='version_id.employee_id',
        store=True,
        string='Employé',
    )
    company_id = fields.Many2one(
        related='version_id.company_id',
        store=True,
    )

    # Dates
    date_etablissement = fields.Date(
        string='Date d\'établissement',
        default=fields.Date.context_today,
        required=True,
    )
    date_effet = fields.Date(
        string='Date d\'effet',
        required=True,
        tracking=True,
    )

    # Type de modification
    avenant_type = fields.Selection([
        ('wage', 'Modification de salaire'),
        ('job', 'Changement de poste'),
        ('schedule', 'Modification d\'horaire'),
        ('workplace', 'Changement de lieu de travail'),
        ('renewal', 'Renouvellement'),
        ('other', 'Autre'),
    ], string='Type d\'avenant', required=True, tracking=True)

    # État
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('confirmed', 'Confirmé'),
        ('applied', 'Appliqué'),
        ('cancelled', 'Annulé'),
    ], string='État', default='draft', tracking=True)

    # Modifications
    # -- Salaire
    old_wage = fields.Monetary(
        string='Ancien salaire',
        currency_field='currency_id',
    )
    new_wage = fields.Monetary(
        string='Nouveau salaire',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='version_id.currency_id',
    )

    # -- Poste
    old_job_id = fields.Many2one(
        'hr.job',
        string='Ancien poste',
    )
    new_job_id = fields.Many2one(
        'hr.job',
        string='Nouveau poste',
    )

    # -- Département
    old_department_id = fields.Many2one(
        'hr.department',
        string='Ancien département',
    )
    new_department_id = fields.Many2one(
        'hr.department',
        string='Nouveau département',
    )

    # -- Horaire
    old_resource_calendar_id = fields.Many2one(
        'resource.calendar',
        string='Ancien horaire',
    )
    new_resource_calendar_id = fields.Many2one(
        'resource.calendar',
        string='Nouvel horaire',
    )

    # Motif
    reason = fields.Text(
        string='Motif',
    )
    notes = fields.Html(
        string='Clauses additionnelles',
    )

    # =========================================================================
    # ONCHANGE
    # =========================================================================
    @api.onchange('version_id')
    def _onchange_version_id(self):
        if self.version_id:
            self.old_wage = self.version_id.wage
            self.old_job_id = self.version_id.job_id
            self.old_department_id = self.version_id.department_id
            self.old_resource_calendar_id = self.version_id.resource_calendar_id

    @api.onchange('avenant_type')
    def _onchange_avenant_type(self):
        """Pré-remplir selon le type"""
        if self.avenant_type and self.version_id:
            self._onchange_version_id()

    # =========================================================================
    # CRUD
    # =========================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('hr.contract.avenant')
        return super().create(vals_list)

    # =========================================================================
    # ACTIONS
    # =========================================================================
    def action_confirm(self):
        """Confirmer l'avenant"""
        self.write({'state': 'confirmed'})

    def action_apply(self):
        """Appliquer l'avenant au contrat"""
        for avenant in self:
            if avenant.state != 'confirmed':
                raise ValidationError(_('L\'avenant doit être confirmé avant d\'être appliqué.'))

            vals = {}

            if avenant.avenant_type == 'wage' and avenant.new_wage:
                vals['wage'] = avenant.new_wage

            if avenant.avenant_type == 'job' and avenant.new_job_id:
                vals['job_id'] = avenant.new_job_id.id

            if avenant.new_department_id:
                vals['department_id'] = avenant.new_department_id.id

            if avenant.avenant_type == 'schedule' and avenant.new_resource_calendar_id:
                vals['resource_calendar_id'] = avenant.new_resource_calendar_id.id

            if vals:
                avenant.version_id.write(vals)

            avenant.state = 'applied'

    def action_cancel(self):
        """Annuler l'avenant"""
        self.write({'state': 'cancelled'})

    def action_draft(self):
        """Remettre en brouillon"""
        self.write({'state': 'draft'})
