
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re


class HrEmployee(models.Model):
    _inherit = 'hr.employee'


    # =========================================================================
    # IDENTIFICATION
    # =========================================================================
    civilite_id = fields.Many2one(
        'hr.civilite',
        string='Civilité',
    )
    matricule = fields.Char(
        string='Matricule',
        readonly=True,
        copy=False,
        index=True,
        help='Matricule interne généré automatiquement'
    )
    matricule_old = fields.Char(
        string='Ancien matricule',
        help='Matricule précédent (migration)'
    )

    # =========================================================================
    # NOM / PRÉNOM SÉPARÉS (convention : NOM EN MAJUSCULES Prénom mixte)
    # =========================================================================
    nom_famille = fields.Char(
        string='Nom de famille',
        help='Extrait automatiquement du champ Nom — mots entièrement en MAJUSCULES.',
    )
    prenom = fields.Char(
        string='Prénom',
        help='Extrait automatiquement du champ Nom — mots avec casse mixte.',
    )

    @api.onchange('name')
    def _onchange_name_split(self):
        """Détecte automatiquement nom/prénom depuis la casse du nom complet."""
        if self.name:
            nom, prenom = self._compute_nom_prenom(self.name)
            self.nom_famille = nom
            self.prenom = prenom

    def _apply_name_split(self, vals):
        """Applique la détection nom/prénom si name est fourni et les champs ne sont pas déjà renseignés."""
        if 'name' in vals and vals['name']:
            if not vals.get('nom_famille') and not vals.get('prenom'):
                nom, prenom = self._compute_nom_prenom(vals['name'])
                if nom:
                    vals['nom_famille'] = nom
                if prenom:
                    vals['prenom'] = prenom
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_name_split(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._apply_name_split(vals)
        return super().write(vals)

    @api.model
    def _compute_nom_prenom(self, full_name):
        """Convention : mots entièrement en MAJUSCULES = nom de famille,
        premier mot en casse mixte et suite = prénom.
        Exemples :
          'BEN ALI Mohamed'    → ('BEN ALI', 'Mohamed')
          'BOUZID Ahmed Nadir' → ('BOUZID', 'Ahmed Nadir')
        """
        words = (full_name or '').strip().split()
        nom_parts, prenom_parts = [], []
        in_prenom = False
        for word in words:
            if not in_prenom and word.isalpha() and word == word.upper():
                nom_parts.append(word)
            else:
                in_prenom = True
                prenom_parts.append(word)
        return ' '.join(nom_parts), ' '.join(prenom_parts)

    # =========================================================================
    # ETAT CIVIL ALGERIEN
    # =========================================================================
    name_ar = fields.Char(
        string='الاسم بالعربية',
        help='Nom complet en arabe tel qu\'il figure sur l\'acte de naissance ou la CNI'
    )
    prenom_pere = fields.Char(
        string='Prénom du père',
    )
    nom_prenom_mere = fields.Char(
        string='Nom et prénom de la mère',
    )
    acte_naissance_num = fields.Char(
        string='N° Acte de naissance',
    )
    date_naissance_presumee = fields.Boolean(
        string='Date présumée',
        help='Cocher si la date de naissance est présumée'
    )

    # =========================================================================
    # PIECE D'IDENTITE (complète les champs natifs)
    # =========================================================================
    # Odoo natif a: identification_id, passport_id, passport_expiration_date
    pi_delivre_par = fields.Char(
        string='Délivrée par',
        help='Daira ou Wilaya ayant délivré la pièce d\'identité'
    )
    pi_date_expiration = fields.Date(
        string='Date expiration PI',
        help='Date d\'expiration de la pièce d\'identité'
    )

    # =========================================================================
    # LOCALISATION NAISSANCE
    # =========================================================================
    # Odoo natif a: place_of_birth, country_of_birth
    # Odoo natif a: private_state_id, private_city pour résidence
    wilaya_naissance_id = fields.Many2one(
        'res.country.state',
        string='Wilaya de naissance',
        domain="[('country_id.code', '=', 'DZ')]",
    )
    commune_naissance_id = fields.Many2one(
        'res.city',
        string='Commune de naissance',
        domain="[('state_id', '=', wilaya_naissance_id)]",
    )

    # =========================================================================
    # SECURITE SOCIALE / CNAS
    # =========================================================================
    # Odoo natif a: ssnid (Social Security Number)
    date_affiliation_cnas = fields.Date(
        string='Date affiliation CNAS',
        help='Date de première affiliation à la sécurité sociale'
    )
    emp_declare = fields.Boolean(
        string='Déclaré CNAS',
        default=True,
        help='Employé déclaré auprès de la CNAS'
    )

    # =========================================================================
    # SITUATION FAMILIALE IRG
    # =========================================================================
    # Odoo natif a: marital, children, spouse_complete_name, spouse_birthdate
    conjoint_travaille = fields.Boolean(
        string='Conjoint travaille',
        help='Si coché, l\'employé perd l\'abattement IRG de marié'
    )
    nb_enfants_charge = fields.Integer(
        string='Enfants à charge',
        help='Nombre d\'enfants à charge pour allocations familiales (CNAS)'
    )
    nb_enfants_scolarises = fields.Integer(
        string='Enfants scolarisés',
        default=0,
        help='Nombre d\'enfants scolarisés pour la prime de scolarité (3 000 DA en septembre)'
    )

    # =========================================================================
    # SERVICE NATIONAL
    # =========================================================================
    service_national_id = fields.Many2one(
        'hr.service.national',
        string='Service national',
        help='Situation vis-à-vis du service national (hommes)'
    )

    # =========================================================================
    # HANDICAP
    # =========================================================================
    is_handicap = fields.Boolean(
        string='Travailleur handicapé',
        help='Ouvre droit à réduction IRG et quota légal 1%'
    )
    handicap_taux = fields.Float(
        string='Taux handicap (%)',
    )

    # =========================================================================
    # CSP / PLACEMENT
    # =========================================================================
    csp_id = fields.Many2one(
        'hr.csp',
        string='Catégorie Socio-Prof.',
    )
    type_placement_id = fields.Many2one(
        'hr.type.placement',
        string='Type de placement',
    )

    # =========================================================================
    # COMPTE BANCAIRE
    # =========================================================================
    # Odoo natif a: bank_account_ids → res.partner.bank
    compte_ccp = fields.Char(
        string='N° CCP',
        help='Numéro de compte CCP (Algérie Poste)'
    )

    # =========================================================================
    # METHODES
    # =========================================================================

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('matricule'):
                vals['matricule'] = self._generate_matricule(vals)
        return super().create(vals_list)

    def _generate_matricule(self, vals=None):
        """Génère le matricule selon la configuration de la société"""
        company = self.env['res.company'].browse(
            vals.get('company_id', self.env.company.id) if vals else self.company_id.id or self.env.company.id
        )

        # Obtenir la séquence
        if company.employee_sequence_id:
            sequence = company.employee_sequence_id._next()
        else:
            sequence = self.env['ir.sequence'].next_by_code('hr.employee.matricule') or '0001'

        pattern = company.matricule_pattern or 'prefix_seq'
        separator = company.matricule_separator or '-'
        prefix = company.matricule_employeur or ''

        if pattern == 'prefix_seq':
            # Format: Préfixe-Séquence (ex: 12345-00001)
            if prefix:
                return f"{prefix}{separator}{sequence}"
            else:
                return sequence
        elif pattern == 'seq_only':
            # Format: Séquence seule (ex: EMP00001)
            return sequence
        elif pattern == 'year_seq':
            # Format: Année/Séquence (ex: 2024/00001)
            from datetime import date
            year = date.today().year
            return f"{year}{separator}{sequence}"
        else:
            # Custom ou fallback
            if prefix:
                return f"{prefix}{separator}{sequence}"
            return sequence

    def action_regenerate_matricule(self):
        """Regénère le matricule (action manuelle si nécessaire)"""
        for employee in self:
            if not employee.matricule:
                employee.matricule = employee._generate_matricule()

    @api.constrains('ssnid')
    def _check_ssnid_format(self):
        """Validation du format du numéro de sécurité sociale algérien (12 chiffres)"""
        for employee in self:
            if employee.ssnid:
                # Nettoyer les espaces et tirets
                ssnid_clean = re.sub(r'[\s\-]', '', employee.ssnid)
                if not re.match(r'^\d{12}$', ssnid_clean):
                    raise ValidationError(
                        _('Le numéro de sécurité sociale doit contenir 12 chiffres.\n'
                          'Format: AA MM WW NNNN S C\n'
                          '(Année/Mois/Wilaya/Numéro/Sexe/Clé)')
                    )

    @api.onchange('wilaya_naissance_id')
    def _onchange_wilaya_naissance(self):
        """Réinitialise la commune si la wilaya change"""
        if self.wilaya_naissance_id != self.commune_naissance_id.state_id:
            self.commune_naissance_id = False

    @api.onchange('commune_naissance_id')
    def _onchange_commune_naissance(self):
        """Auto-remplit le lieu de naissance"""
        if self.commune_naissance_id and not self.place_of_birth:
            self.place_of_birth = self.commune_naissance_id.name

    @api.onchange('job_id')
    def _onchange_job_id_csp(self):
        """Auto-remplit la CSP depuis le poste"""
        if self.job_id and self.job_id.csp_id:
            self.csp_id = self.job_id.csp_id

    def action_print_attestation_travail(self):
        """Imprime l'attestation de travail"""
        self.ensure_one()
        return self.env.ref('hr_dz_base.action_report_attestation_travail').report_action(self)

    def action_print_certificat_travail(self):
        """Imprime le certificat de travail"""
        self.ensure_one()
        return self.env.ref('hr_dz_base.action_report_certificat_travail').report_action(self)
