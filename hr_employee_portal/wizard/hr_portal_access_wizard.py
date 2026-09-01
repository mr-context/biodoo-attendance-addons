import secrets
import string
import unicodedata

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrPortalAccessWizard(models.TransientModel):
    _name = 'hr.portal.access.wizard'
    _description = 'Wizard Accès Portail Employé'

    employee_id = fields.Many2one(
        'hr.employee', string='Employé', readonly=True, required=True)
    login = fields.Char(string='Identifiant')
    password = fields.Char(string='Mot de passe')
    email = fields.Char(string='Email')
    send_email = fields.Boolean(string='Envoyer invitation par email', default=True)
    existing_user = fields.Many2one('res.users', string='Compte existant', readonly=True)
    mode = fields.Selection([
        ('create',   'Créer'),
        ('reset',    'Réinitialiser'),
        ('revoke',   'Révoquer'),
        ('internal', 'Compte interne'),
    ], string='Mode', default='create', required=True)

    # -------------------------------------------------------------------------
    # Defaults
    # -------------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        employee_id = res.get('employee_id') or self.env.context.get('default_employee_id')
        if employee_id:
            emp = self.env['hr.employee'].browse(employee_id)
            existing_user = emp.user_id or False
            if not existing_user and emp.work_email:
                existing_user = self.env['res.users'].sudo().search(
                    [('email', '=', emp.work_email), ('active', '=', True)], limit=1)
            if existing_user and existing_user.share:
                # Compte portail existant → réinitialisation
                res['mode'] = 'reset'
                res['existing_user'] = existing_user.id
                res['login'] = existing_user.login
                res['email'] = existing_user.email or emp.work_email or ''
                res['password'] = self._generate_password()
            elif existing_user and not existing_user.share:
                # Compte interne existant → pas de portail possible
                res['mode'] = 'internal'
                res['existing_user'] = existing_user.id
                res['login'] = existing_user.login
                res['email'] = existing_user.email or emp.work_email or ''
            else:
                res['mode'] = 'create'
                res['login'] = self._generate_unique_login(emp)
                res['password'] = self._generate_password()
                res['email'] = emp.work_email or ''
        return res

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @api.model
    def _generate_unique_login(self, employee):
        """Generate a unique login from employee name: prenom.nom (ASCII, lowercase)."""
        name = employee.name or ''
        normalized = unicodedata.normalize('NFKD', name)
        ascii_name = normalized.encode('ascii', 'ignore').decode('ascii')
        parts = ascii_name.lower().split()
        if len(parts) >= 2:
            base_login = '{}.{}'.format(parts[0], parts[-1])
        elif parts:
            base_login = parts[0]
        else:
            base_login = 'employee_{}'.format(employee.id)
        # Keep only alphanumeric + dot
        base_login = ''.join(c for c in base_login if c.isalnum() or c == '.')
        # Ensure uniqueness
        login = base_login
        suffix = 2
        while self.env['res.users'].sudo().search_count([('login', '=', login)]):
            login = '{}{}'.format(base_login, suffix)
            suffix += 1
        return login

    @staticmethod
    def _generate_password():
        """Generate a simple 10-character alphanumeric password (A-Z, a-z, 0-9)."""
        alphabet = string.ascii_letters + string.digits
        # Guarantee at least one uppercase, one lowercase, one digit
        pwd = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
        ]
        pwd += [secrets.choice(alphabet) for _ in range(7)]
        secrets.SystemRandom().shuffle(pwd)
        return ''.join(pwd)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_confirm(self):
        self.ensure_one()
        if self.mode == 'create':
            return self._create_portal_user()
        elif self.mode == 'reset':
            return self._reset_password()
        elif self.mode == 'revoke':
            return self._revoke_access()
        elif self.mode == 'internal':
            raise UserError(_(
                "L'employé '%s' possède déjà un compte utilisateur interne (%s). "
                "Un compte interne donne accès au backend Odoo — aucun compte portail "
                "séparé ne peut être créé."
            ) % (self.employee_id.name, self.existing_user.login))
        raise UserError(_('Mode inconnu : %s') % self.mode)

    def action_set_minimal_access(self):
        """Retire tous les groupes applicatifs — garde uniquement base.group_user et ses implied."""
        self.ensure_one()
        user = self.existing_user
        if not user:
            raise UserError(_("Aucun utilisateur trouvé."))
        group_user = self.env.ref('base.group_user')

        # Calcul récursif des groupes à conserver (group_user + tout ce qu'il implique)
        def collect_implied(group, seen=None):
            if seen is None:
                seen = set()
            if group.id in seen:
                return seen
            seen.add(group.id)
            for implied in group.implied_ids:
                collect_implied(implied, seen)
            return seen

        keep_ids = collect_implied(group_user)
        to_remove = user.groups_id.filtered(lambda g: g.id not in keep_ids)
        if to_remove:
            user.sudo().write({'groups_id': [(3, g.id) for g in to_remove]})
        return {'type': 'ir.actions.act_window_close'}

    def action_revoke(self):
        """Dedicated revoke button (available in reset mode too)."""
        self.ensure_one()
        return self._revoke_access()

    # -------------------------------------------------------------------------
    # Internal implementations
    # -------------------------------------------------------------------------

    def _create_portal_user(self):
        employee = self.employee_id
        if not self.login:
            raise UserError(_("L'identifiant est requis."))
        if not self.password:
            raise UserError(_("Le mot de passe est requis."))
        if self.env['res.users'].sudo().search_count([('login', '=', self.login)]):
            raise UserError(_("L'identifiant '%s' est déjà utilisé.") % self.login)

        group_portal = self.env.ref('base.group_portal')
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': employee.name,
            'login': self.login,
            'password': self.password,
            'email': self.email or False,
            'group_ids': [(6, 0, [group_portal.id])],
        })
        employee.sudo().write({'user_id': user.id})

        if self.send_email:
            tmpl = self.env.ref(
                'portal.mail_template_data_portal_welcome', raise_if_not_found=False)
            if tmpl:
                tmpl.with_context(lang=user.lang).send_mail(user.id, force_send=True)

        return self.env.ref(
            'hr_employee_portal.action_report_portal_access').report_action(self)

    def _reset_password(self):
        user = self.existing_user
        if not user:
            raise UserError(_("Aucun utilisateur portail trouvé pour cet employé."))
        if not self.password:
            raise UserError(_("Le nouveau mot de passe est requis."))
        user.sudo().write({'password': self.password})
        return self.env.ref(
            'hr_employee_portal.action_report_portal_access').report_action(self)

    def _revoke_access(self):
        user = self.existing_user or self.employee_id.user_id
        if not user:
            raise UserError(_("Aucun utilisateur portail trouvé pour cet employé."))
        user.sudo().write({'active': False})
        self.employee_id.sudo().write({'user_id': False})
        return {'type': 'ir.actions.act_window_close'}
