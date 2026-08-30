"""
Extension du modèle hr.version pour la gestion des contrats algériens.

Note: Dans Odoo 19, hr.contract n'existe plus. Les contrats sont gérés
via hr.version qui contient toutes les informations contractuelles.
"""

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class HrVersion(models.Model):
    """Extension de hr.version pour les contrats algériens"""
    _inherit = 'hr.version'

    # contract_type_id existe déjà dans hr.version (Odoo 19)

    # =========================================================================
    # STATUT DU CONTRAT
    # =========================================================================
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('pending', 'En attente signature'),
        ('active', 'Actif'),
        ('expired', 'Expire'),
        ('terminated', 'Resilie'),
    ], string='Statut', default='draft', tracking=True, index=True,
       help="Brouillon: version technique creee automatiquement, pas un vrai contrat")

    is_real_contract = fields.Boolean(
        string='Contrat reel',
        compute='_compute_is_real_contract',
        store=True,
        help="Indique si c'est un vrai contrat (pas un brouillon technique)"
    )

    @api.depends('state')
    def _compute_is_real_contract(self):
        for version in self:
            version.is_real_contract = version.state not in ('draft', False)

    @api.depends('contract_reference', 'employee_id', 'date_version')
    def _compute_display_name(self):
        """Affiche le numéro de contrat au lieu de la date"""
        for version in self:
            if version.contract_reference:
                version.display_name = version.contract_reference
            elif version.employee_id:
                version.display_name = f"{version.employee_id.name} - {version.date_version}"
            else:
                version.display_name = version.name or str(version.date_version)

    def action_confirm(self):
        """Confirme le brouillon ou le pending en contrat actif"""
        for version in self:
            if version.state in ('draft', 'pending'):
                vals = {'state': 'active'}
                # Generer le numero de contrat si pas encore fait
                if not version.contract_reference:
                    vals['contract_reference'] = self._generate_contract_reference()
                version.write(vals)

    def action_set_pending(self):
        """Met en attente de signature"""
        # Generer le numero de contrat
        for version in self:
            vals = {'state': 'pending'}
            if not version.contract_reference:
                vals['contract_reference'] = self._generate_contract_reference()
            version.write(vals)

    def action_sign(self):
        """Signe le contrat - passe de pending a active"""
        for version in self:
            if version.state != 'pending':
                raise ValidationError(_('Seuls les contrats en attente de signature peuvent etre signes.'))

            version.write({'state': 'active'})

            # Log dans le chatter
            version.message_post(
                body=_('Contrat signe le %s par %s') % (
                    fields.Date.context_today(self),
                    self.env.user.name,
                ),
                subject=_('Signature du contrat'),
            )

    def action_print_contract(self):
        """Imprimer le contrat"""
        self.ensure_one()
        return self.env.ref('hr_dz_contract.action_report_contract_version').report_action(self)

    def action_terminate(self):
        """Resilie le contrat"""
        self.write({'state': 'terminated'})

    def action_set_expired(self):
        """Marque comme expire"""
        self.write({'state': 'expired'})

    # =========================================================================
    # REFERENCES
    # =========================================================================
    contract_reference = fields.Char(
        string='N° Contrat',
        readonly=True,
        copy=False,
        index=True,
        groups="hr.group_hr_manager",
    )
    date_etablissement = fields.Date(
        string='Date d\'établissement',
        default=fields.Date.context_today,
        tracking=True,
        groups="hr.group_hr_manager",
    )

    # =========================================================================
    # DUREE CONTRAT (CDD)
    # =========================================================================
    is_cdd = fields.Boolean(
        string='Durée déterminée',
        compute='_compute_is_cdd',
        store=True,
    )
    duration_months = fields.Integer(
        string='Durée (mois)',
        tracking=True,
        groups="hr.group_hr_manager",
    )
    renewal_count = fields.Integer(
        string='N° Renouvellement',
        default=0,
        readonly=True,
        groups="hr.group_hr_manager",
    )
    parent_version_id = fields.Many2one(
        'hr.version',
        string='Contrat initial',
        help='Pour les renouvellements, référence au contrat d\'origine',
        groups="hr.group_hr_manager",
    )

    # =========================================================================
    # PERIODE D'ESSAI (extension)
    # =========================================================================
    # Redefinir trial_date_end pour enlever la restriction groups du core
    trial_date_end = fields.Date(
        string='Fin période d\'essai',
        help='Date de fin de la période d\'essai',
        tracking=True,
        groups='',  # Enleve la restriction du core
    )
    trial_duration_months = fields.Integer(
        string='Durée essai (mois)',
        default=3,
        help='Durée légale : 0 à 6 mois selon la catégorie professionnelle (Loi 90-11, Art. 20).',
    )

    @api.constrains('trial_duration_months')
    def _check_trial_duration(self):
        for version in self:
            if version.trial_duration_months < 0 or version.trial_duration_months > 6:
                raise ValidationError(
                    _('Durée période d\'essai : 0 à 6 mois maximum (Loi algérienne 90-11, Art. 20).')
                )
    trial_state = fields.Selection([
        ('pending', 'En attente'),
        ('ongoing', 'En cours'),
        ('extended', 'Prolongée'),
        ('confirmed', 'Confirmé'),
        ('failed', 'Non concluante'),
    ], string='État période d\'essai', default='pending', tracking=True)
    trial_extension_count = fields.Integer(
        string='Prolongations essai',
        default=0,
    )
    trial_notified = fields.Boolean(
        string='Notification envoyée',
        default=False,
    )
    contract_end_notified = fields.Boolean(
        string='Notification fin contrat envoyée',
        default=False,
    )
    has_trial_period = fields.Boolean(
        string='Avec période d\'essai',
        compute='_compute_has_trial_period',
        store=True,
    )
    trial_warning_level = fields.Selection([
        ('none', 'Aucun'),
        ('ongoing', 'En cours'),
        ('warning', 'Fin proche'),
        ('danger', 'Dépassée'),
    ], string='Alerte essai', compute='_compute_trial_warning_level')

    @api.depends('trial_date_end', 'trial_state', 'state')
    def _compute_trial_warning_level(self):
        today = fields.Date.context_today(self)
        for version in self:
            if version.state not in ('active', 'pending') or not version.trial_date_end:
                version.trial_warning_level = 'none'
            elif version.trial_state == 'confirmed':
                version.trial_warning_level = 'none'
            elif today > version.trial_date_end:
                version.trial_warning_level = 'danger'  # Depassee sans decision
            elif (version.trial_date_end - today).days <= 7:
                version.trial_warning_level = 'warning'  # Moins de 7 jours
            else:
                version.trial_warning_level = 'ongoing'  # En cours normal

    # =========================================================================
    # PREAVIS
    # =========================================================================
    notice_period = fields.Integer(
        string='Préavis',
    )
    notice_period_uom = fields.Selection([
        ('days', 'Jours'),
        ('weeks', 'Semaines'),
        ('months', 'Mois'),
    ], string='Unité préavis', default='months')

    # =========================================================================
    # AVENANTS
    # =========================================================================
    avenant_ids = fields.One2many(
        'hr.contract.avenant',
        'version_id',
        string='Avenants',
    )
    avenant_count = fields.Integer(
        compute='_compute_avenant_count',
        string='Nb Avenants',
    )

    # =========================================================================
    # MODELE ET ARTICLES
    # =========================================================================
    modele_id = fields.Many2one(
        'hr.contract.modele',
        string='Modèle de contrat',
        groups="hr.group_hr_manager",
    )
    article_line_ids = fields.One2many(
        'hr.contract.article.line',
        'version_id',
        string='Articles du contrat',
        copy=True,
    )

    # =========================================================================
    # SALAIRE ET INDEMNITÉS
    # =========================================================================
    wage_type = fields.Selection([
        ('gross', 'Brut'),
        ('net', 'Net'),
        ('base', 'Salaire de base'),
    ], string='Type salaire', default='gross',
       groups="hr.group_hr_manager")

    transport_allowance_enabled = fields.Boolean(
        string='Indemnité transport activée',
        default=True,
        help='Décocher pour désactiver l\'indemnité de transport sur ce contrat.',
    )
    daily_transport_allowance = fields.Float(
        string='Indemnité transport / jour',
        default=200.0,
        help='Montant journalier de l\'indemnité de transport (par jour de présence)',
    )
    meal_allowance_enabled = fields.Boolean(
        string='Indemnité panier activée',
        default=True,
        help='Décocher pour désactiver l\'indemnité de panier sur ce contrat.',
    )
    daily_meal_allowance = fields.Float(
        string='Indemnité panier / jour',
        default=400.0,
        help='Montant journalier de l\'indemnité de panier (par jour de présence)',
    )
    prime_line_ids = fields.One2many(
        'hr.version.prime.line',
        'version_id',
        string='Primes contractuelles',
        copy=True,
    )

    # =========================================================================
    # HORAIRES / SHIFT
    # =========================================================================
    work_schedule_type = fields.Selection([
        ('normal', 'Normal (jour)'),
        ('shift', 'Posté (3x8, 2x12...)'),
        ('night', 'Nuit fixe'),
        ('flexible', 'Flexible'),
    ], string='Type d\'horaire', default='normal',
       groups="hr.group_hr_manager")
    is_overnight_shift = fields.Boolean(
        string='Shift chevauchant minuit',
        help='Ex: 22h - 6h. Affecte le calcul des présences.',
        groups="hr.group_hr_manager",
    )
    working_days_per_month = fields.Integer(
        string='Jours ouvrables / mois',
        compute='_compute_working_schedule',
        store=True,
        readonly=False,
        default=22,
        help='Jours ouvrables mensuels déduits du calendrier (22 pour 5j/sem, 26 pour 6j/sem)',
    )
    monthly_hours = fields.Float(
        string='Heures mensuelles',
        compute='_compute_working_schedule',
        store=True,
        readonly=False,
        digits=(8, 2),
        default=173.33,
        help='Heures mensuelles moyennes déduites du calendrier (heures/sem × 52 / 12)',
    )

    # =========================================================================
    # METHODES COMPUTE
    # =========================================================================
    @api.depends('resource_calendar_id')
    def _compute_working_schedule(self):
        for version in self:
            calendar = version.resource_calendar_id
            if calendar:
                days_per_week = calendar._get_days_per_week()
                version.working_days_per_month = round(days_per_week * 52 / 12)
                version.monthly_hours = round(calendar.hours_per_week * 52 / 12, 2)
            else:
                version.working_days_per_month = 22
                version.monthly_hours = 173.33

    @api.depends('contract_type_id', 'contract_type_id.is_cdd')
    def _compute_is_cdd(self):
        for version in self:
            version.is_cdd = version.contract_type_id.is_cdd if version.contract_type_id else False

    @api.depends('trial_date_end')
    def _compute_has_trial_period(self):
        for version in self:
            version.has_trial_period = bool(version.trial_date_end)

    @api.depends('avenant_ids')
    def _compute_avenant_count(self):
        for version in self:
            version.avenant_count = len(version.avenant_ids)

    # =========================================================================
    # ONCHANGE
    # =========================================================================
    @api.onchange('contract_type_id')
    def _onchange_contract_type_id_dz(self):
        if self.contract_type_id and self.contract_type_id.has_trial_period:
            self.trial_duration_months = self.contract_type_id.default_trial_months
            if self.contract_date_start:
                self.trial_date_end = self.contract_date_start + relativedelta(
                    months=self.trial_duration_months
                ) - relativedelta(days=1)

    @api.onchange('contract_date_start', 'duration_months')
    def _onchange_duration(self):
        if self.contract_date_start and self.duration_months and self.is_cdd:
            self.contract_date_end = self.contract_date_start + relativedelta(
                months=self.duration_months
            ) - relativedelta(days=1)

    @api.onchange('contract_date_start', 'trial_duration_months')
    def _onchange_trial_period(self):
        if self.contract_date_start and self.trial_duration_months:
            self.trial_date_end = self.contract_date_start + relativedelta(
                months=self.trial_duration_months
            ) - relativedelta(days=1)

    @api.onchange('date_etablissement')
    def _onchange_date_etablissement(self):
        """Synchroniser date_etablissement avec contract_date_start si vide"""
        if self.date_etablissement and not self.contract_date_start:
            self.contract_date_start = self.date_etablissement

    @api.onchange('contract_date_start')
    def _onchange_contract_date_start_sync(self):
        """Synchroniser contract_date_start avec date_etablissement si vide"""
        if self.contract_date_start and not self.date_etablissement:
            self.date_etablissement = self.contract_date_start

    @api.onchange('job_id')
    def _onchange_job_id_notice(self):
        """Recupere le preavis depuis le poste si disponible"""
        if self.job_id:
            if hasattr(self.job_id, 'preavis_duree') and self.job_id.preavis_duree:
                self.notice_period = self.job_id.preavis_duree
            if hasattr(self.job_id, 'preavis_uom') and self.job_id.preavis_uom:
                # Les cles sont maintenant alignees (days, weeks, months)
                if self.job_id.preavis_uom in ('days', 'weeks', 'months'):
                    self.notice_period_uom = self.job_id.preavis_uom

    @api.onchange('modele_id')
    def _onchange_modele_id(self):
        """Remplit les articles depuis le modèle sélectionné"""
        if self.modele_id:
            # Mettre à jour le type de contrat
            if self.modele_id.contract_type_id:
                self.contract_type_id = self.modele_id.contract_type_id
            # Remplir les articles
            self._fill_articles_from_modele()

    def _fill_articles_from_modele(self):
        """Copie les articles du modèle vers le contrat"""
        if not self.modele_id:
            return
        # Supprimer les articles existants
        self.article_line_ids = [(5, 0, 0)]
        # Ajouter les articles du modèle
        lines = []
        for modele_line in self.modele_id.article_line_ids:
            lines.append((0, 0, {
                'article_id': modele_line.article_id.id,
                'sequence': modele_line.sequence,
            }))
        self.article_line_ids = lines

    def get_contract_variables(self):
        """Retourne les variables pour les placeholders des articles"""
        self.ensure_one()

        # Formatage du salaire en lettres (simplifié)
        def number_to_words(n):
            # Simplifié - à améliorer avec une vraie conversion
            return str(n)

        return {
            'civilite': self.employee_id.civilite_id.name if hasattr(self.employee_id, 'civilite_id') and self.employee_id.civilite_id else '',
            'employe': self.employee_id.name if self.employee_id else '',
            'poste': self.job_id.name if self.job_id else '',
            'type_contrat': self.contract_type_id.name if self.contract_type_id else '',
            'date_debut': self.contract_date_start.strftime('%d/%m/%Y') if self.contract_date_start else '',
            'date_fin': self.contract_date_end.strftime('%d/%m/%Y') if self.contract_date_end else '',
            'salaire': '{:,.2f}'.format(self.wage).replace(',', ' ') if self.wage else '0',
            'salaire_lettres': number_to_words(self.wage) if self.wage else '',
            'duree_essai': str(self.trial_duration_months) if self.trial_duration_months else '',
            'duree_preavis': str(self.notice_period) if self.notice_period else '',
            'departement': self.department_id.name if self.department_id else '',
            'duree_contrat_mois': str(self.duration_months) if self.duration_months else '',
            'societe': self.company_id.name if self.company_id else '',
            'adresse_societe': self.company_id.street if self.company_id else '',
            'nif': self.company_id.nif if hasattr(self.company_id, 'nif') and self.company_id.nif else '',
            'nis': self.company_id.nis if hasattr(self.company_id, 'nis') and self.company_id.nis else '',
            'rc': self.company_id.rc if hasattr(self.company_id, 'rc') and self.company_id.rc else '',
        }

    # =========================================================================
    # CRUD
    # =========================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            state = vals.get('state', 'draft')
            # Generer le numero seulement si explicitement actif et avec date
            if state not in ('draft', False) and vals.get('contract_date_start') and not vals.get('contract_reference'):
                vals['contract_reference'] = self._generate_contract_reference(vals)

        versions = super().create(vals_list)
        for version in versions:
            if version.state not in ('draft', False):
                version._update_trial_state()
        return versions

    def _generate_contract_reference(self, vals=None):
        """Genere le numero de contrat selon la configuration de la societe

        Si une sequence est configuree sur la societe, elle est utilisee telle quelle
        (la sequence gere elle-meme son format avec prefixe/suffixe).
        Sinon, on utilise le pattern configure.
        """
        from datetime import date

        company = self.env['res.company'].browse(
            vals.get('company_id', self.env.company.id) if vals else self.company_id.id or self.env.company.id
        )

        # Si une sequence specifique est configuree, l'utiliser directement
        if company.contract_sequence_id:
            return company.contract_sequence_id._next()

        # Sinon, utiliser la sequence par defaut avec le pattern configure
        sequence = self.env['ir.sequence'].next_by_code('hr.contract.dz') or '00001'

        pattern = company.contract_pattern or 'seq_only'
        separator = company.contract_separator or '-'
        prefix = company.contract_prefix or 'CTR'

        if pattern == 'prefix_seq':
            # Format: Prefixe-Annee-Sequence (ex: CTR-2024-00001)
            year = date.today().year
            return f"{prefix}{separator}{year}{separator}{sequence}"
        elif pattern == 'year_seq':
            # Format: Annee/Sequence (ex: 2024/00001)
            year = date.today().year
            return f"{year}{separator}{sequence}"
        elif pattern == 'type_seq':
            # Format: Type-Sequence (ex: CDI-00001)
            contract_type = None
            if vals and vals.get('contract_type_id'):
                contract_type = self.env['hr.contract.type'].browse(vals['contract_type_id'])
            elif self.contract_type_id:
                contract_type = self.contract_type_id
            type_code = contract_type.code if contract_type and contract_type.code else 'CTR'
            return f"{type_code}{separator}{sequence}"
        else:
            # seq_only ou fallback - juste la sequence
            return sequence

    # =========================================================================
    # ACTIONS
    # =========================================================================
    def action_view_avenants(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Avenants'),
            'res_model': 'hr.contract.avenant',
            'view_mode': 'list,form',
            'domain': [('version_id', '=', self.id)],
            'context': {'default_version_id': self.id},
        }

    def action_open_trial_wizard(self):
        """Ouvrir le wizard de confirmation/prolongation d'essai"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Période d\'essai'),
            'res_model': 'hr.trial.confirmation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_version_id': self.id},
        }

    def action_open_renewal_wizard(self):
        """Ouvrir le wizard de renouvellement"""
        self.ensure_one()
        if not self.is_cdd:
            raise ValidationError(_('Seuls les CDD peuvent être renouvelés.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Renouvellement de contrat'),
            'res_model': 'hr.contract.renewal.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_version_id': self.id},
        }

    # =========================================================================
    # PERIODE D'ESSAI
    # =========================================================================
    def _update_trial_state(self):
        """Met à jour l'état de la période d'essai"""
        today = fields.Date.context_today(self)
        for version in self:
            if not version.trial_date_end:
                version.trial_state = 'pending'
                continue

            if version.trial_state == 'confirmed':
                continue

            if version.contract_date_start and version.trial_date_end:
                if today < version.contract_date_start:
                    version.trial_state = 'pending'
                elif today <= version.trial_date_end:
                    if version.trial_extension_count > 0:
                        version.trial_state = 'extended'
                    else:
                        version.trial_state = 'ongoing'
                else:
                    if version.trial_state not in ('confirmed', 'failed'):
                        version.trial_state = 'confirmed'

    def confirm_trial_period(self):
        """Confirme la période d'essai"""
        self.ensure_one()
        self.trial_state = 'confirmed'

    def extend_trial_period(self, months):
        """Prolonge la période d'essai"""
        self.ensure_one()
        if not self.trial_date_end:
            return
        new_end = self.trial_date_end + relativedelta(months=months)
        self.write({
            'trial_date_end': new_end,
            'trial_extension_count': self.trial_extension_count + 1,
            'trial_state': 'extended',
        })

    def fail_trial_period(self):
        """Marque la période d'essai comme non concluante"""
        self.ensure_one()
        self.trial_state = 'failed'

    # =========================================================================
    # CRON - ALERTES
    # =========================================================================
    @api.model
    def _cron_trial_period_alerts(self):
        """Envoie des alertes 7 jours avant la fin de période d'essai"""
        today = fields.Date.context_today(self)
        alert_date = today + relativedelta(days=7)

        versions = self.search([
            ('contract_date_start', '!=', False),
            ('trial_date_end', '>=', today),
            ('trial_date_end', '<=', alert_date),
            ('trial_state', 'in', ['ongoing', 'extended']),
            ('trial_notified', '=', False),
        ])

        for version in versions:
            version._send_trial_alert()
            version.trial_notified = True

    def _send_trial_alert(self):
        """Envoie une alerte de fin de période d'essai"""
        self.ensure_one()
        if not self.employee_id:
            return
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            date_deadline=self.trial_date_end,
            summary=_('Fin de période d\'essai: %s') % self.employee_id.name,
            note=_('La période d\'essai de %s se termine le %s. '
                   'Veuillez confirmer ou prolonger.') % (
                self.employee_id.name, self.trial_date_end),
            user_id=self.hr_responsible_id.id if self.hr_responsible_id else self.env.user.id,
        )

    @api.model
    def _cron_contract_end_alerts(self):
        """Envoie des alertes 30 jours avant la fin de contrat CDD"""
        today = fields.Date.context_today(self)
        alert_date = today + relativedelta(days=30)

        versions = self.search([
            ('contract_date_start', '!=', False),
            ('contract_date_end', '>=', today),
            ('contract_date_end', '<=', alert_date),
            ('is_cdd', '=', True),
            ('contract_end_notified', '=', False),
        ])

        for version in versions:
            if version.employee_id:
                version.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=version.contract_date_end,
                    summary=_('Fin de contrat CDD: %s') % version.employee_id.name,
                    note=_('Le contrat CDD de %s se termine le %s. '
                           'Renouveler ou préparer la sortie.') % (
                        version.employee_id.name, version.contract_date_end),
                )
                version.contract_end_notified = True

    # =========================================================================
    # SURCHARGE CONTRAINTES ODOO - Permettre suppression/archivage
    # =========================================================================
    def unlink(self):
        """Permet la suppression des contrats meme si c'est le dernier

        Note: Odoo natif empeche la suppression du dernier contrat d'un employe.
        Cette surcharge permet plus de flexibilite pour la gestion algerienne.
        """
        # Sauvegarder les employes avant suppression pour nettoyer current_version_id
        employees = self.mapped('employee_id')
        result = super(HrVersion, self).unlink()
        # Mettre a jour current_version_id si necessaire
        for employee in employees:
            if employee.exists():
                remaining = employee.version_ids.filtered('active')
                if remaining:
                    employee.sudo().current_version_id = remaining[0]
                elif not employee.current_version_id or not employee.current_version_id.exists():
                    # Creer un nouveau brouillon pour eviter les erreurs hr.employee.public
                    new_version = self.sudo().create({
                        'employee_id': employee.id,
                        'company_id': employee.company_id.id,
                        'date_version': self._unique_date_for(employee),
                        'state': 'draft',
                    })
                    employee.sudo().current_version_id = new_version.id
        return result

    def _unique_date_for(self, employee):
        """Retourne une date_version unique pour l'employe (today, ou today+N si déjà prise)."""
        from datetime import timedelta
        candidate = fields.Date.today()
        existing_dates = set(
            self.sudo().search([
                ('employee_id', '=', employee.id),
                ('active', '=', True),
            ]).mapped('date_version')
        )
        while candidate in existing_dates:
            candidate += timedelta(days=1)
        return candidate

    def write(self, vals):
        """Surcharge write() pour:
        - Mettre a jour l'etat de periode d'essai
        - Permettre l'archivage des contrats meme si c'est le dernier
        """
        # Cas special: archivage - creer le brouillon AVANT pour satisfaire
        # la contrainte native "Cannot archive the only active record of an employee"
        if 'active' in vals and not vals['active']:
            employees = self.mapped('employee_id')
            for version in self:
                if len(version.employee_id.version_ids.filtered('active')) == 1:
                    new_version = self.sudo().create({
                        'employee_id': version.employee_id.id,
                        'company_id': version.employee_id.company_id.id,
                        'date_version': self._unique_date_for(version.employee_id),
                        'state': 'draft',
                    })
                    version.employee_id.sudo().current_version_id = new_version.id
            result = super().write(vals)
            # Mettre a jour current_version_id si une autre version active existe
            for employee in employees:
                if employee.exists():
                    remaining = employee.version_ids.filtered('active')
                    if remaining:
                        employee.sudo().current_version_id = remaining[0]
            return result

        # Cas special: changement d'employe - creer un brouillon pour l'employe
        # original avant de reassigner, pour satisfaire la contrainte native
        if 'employee_id' in vals:
            for version in self.filtered(
                lambda v: v.employee_id.id != vals['employee_id']
                and len(v.employee_id.version_ids.filtered('active')) == 1
            ):
                self.sudo().create({
                    'employee_id': version.employee_id.id,
                    'company_id': version.employee_id.company_id.id,
                    'date_version': self._unique_date_for(version.employee_id),
                    'state': 'draft',
                })

        result = super().write(vals)

        # Mettre a jour l'etat de periode d'essai si date debut change
        if 'contract_date_start' in vals:
            for version in self:
                version._update_trial_state()

        return result
