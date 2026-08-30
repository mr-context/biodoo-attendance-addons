"""
Extension du modele hr.employee pour ajouter les champs related
des contrats algeriens.
"""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    """Extension de hr.employee avec les champs contrat DZ"""
    _inherit = 'hr.employee'

    @api.model_create_multi
    def create(self, vals_list):
        """Cree automatiquement une version pour chaque nouvel employe.

        Note: Odoo 19 exige qu'un employe ait toujours une version (hr.version)
        car plusieurs modules natifs (hr_skills, hr_employee_public) en dependent.
        """
        employees = super().create(vals_list)

        for employee in employees:
            if not employee.current_version_id:
                try:
                    version = self.env['hr.version'].sudo().create({
                        'employee_id': employee.id,
                        'company_id': employee.company_id.id or self.env.company.id,
                        'date_version': fields.Date.today(),
                    })
                    employee.sudo().write({'current_version_id': version.id})
                except Exception as e:
                    _logger.warning(
                        "Impossible de créer la version contractuelle pour %s : %s",
                        employee.name, e,
                    )

        return employees

    def unlink(self):
        """Supprime proprement l'employe et ses donnees liees.

        hr_version.employee_id a ON DELETE SET NULL : supprimer l'employé rend
        ses versions orphelines (employee_id=NULL). On collecte les IDs avant,
        supprime l'employé, puis nettoie les versions orphelines.
        """
        version_ids = self.env['hr.version'].sudo().search([
            ('employee_id', 'in', self.ids)
        ]).ids

        result = super().unlink()

        # Les versions ont maintenant employee_id=NULL → on peut les supprimer
        # sans déclencher _unlink_except_last_version (employee_id = False)
        if version_ids:
            orphaned = self.env['hr.version'].sudo().browse(version_ids).exists()
            if orphaned:
                orphaned.sudo().unlink()

        return result

    @api.model
    def _fix_employees_without_version(self):
        """Corrige les employes sans contrat/version (appele au demarrage ou manuellement)"""
        employees_without_version = self.sudo().search([
            ('current_version_id', '=', False)
        ])

        for employee in employees_without_version:
            # Chercher une version existante non liee
            existing_version = self.env['hr.version'].sudo().search([
                ('employee_id', '=', employee.id)
            ], limit=1)

            if existing_version:
                employee.current_version_id = existing_version.id
            else:
                # Creer une nouvelle version
                version = self.env['hr.version'].sudo().create({
                    'employee_id': employee.id,
                    'company_id': employee.company_id.id or self.env.company.id,
                    'date_version': fields.Date.today(),
                })
                employee.current_version_id = version.id

        return len(employees_without_version)

    @api.model
    def _fix_employee_public_view(self):
        """
        Corrige la vue hr_employee_public pour utiliser LEFT JOIN au lieu de JOIN.
        Cela permet aux employes sans contrat d'exister sans erreur.
        """
        # Recuperer la definition actuelle de la vue
        self.env.cr.execute("""
            SELECT pg_get_viewdef('hr_employee_public'::regclass, true)
        """)
        view_def = self.env.cr.fetchone()[0]

        # Verifier si c'est un JOIN (pas LEFT JOIN)
        if ' JOIN hr_version v ON' in view_def and 'LEFT JOIN hr_version' not in view_def:
            # Remplacer JOIN par LEFT JOIN
            new_view_def = view_def.replace(
                'JOIN hr_version v ON',
                'LEFT JOIN hr_version v ON'
            )

            # Recreer la vue
            self.env.cr.execute("DROP VIEW IF EXISTS hr_employee_public CASCADE")
            self.env.cr.execute(f"CREATE OR REPLACE VIEW hr_employee_public AS {new_view_def}")

            return True
        return False

    # Related fields from current_version_id (hr.version)
    contract_reference = fields.Char(
        string='N° Contrat',
        related='current_version_id.contract_reference',
        readonly=True,
        groups="hr.group_hr_manager",
    )
    trial_state = fields.Selection(
        related='current_version_id.trial_state',
        string='État période d\'essai',
        readonly=True,
    )

    # Compteur de contrats (versions reelles, pas les brouillons)
    contract_count = fields.Integer(
        compute='_compute_contract_count',
        string='Nb Contrats',
    )

    @api.depends('version_ids', 'version_ids.state')
    def _compute_contract_count(self):
        for employee in self:
            # Ne compte que les vrais contrats (pas les brouillons)
            real_contracts = employee.version_ids.filtered(
                lambda v: v.state and v.state != 'draft'
            )
            employee.contract_count = len(real_contracts)

    def action_open_contract(self):
        """Ouvrir le(s) contrat(s) de l'employé"""
        self.ensure_one()

        if self.contract_count > 1:
            # Plusieurs contrats: ouvrir la liste
            return {
                'type': 'ir.actions.act_window',
                'name': _('Contrats de %s') % self.name,
                'res_model': 'hr.version',
                'view_mode': 'list,form',
                'domain': [('employee_id', '=', self.id)],
                'context': {'default_employee_id': self.id},
            }
        elif self.current_version_id:
            # Un seul contrat: l'ouvrir directement
            return {
                'type': 'ir.actions.act_window',
                'name': _('Contrat'),
                'res_model': 'hr.version',
                'res_id': self.current_version_id.id,
                'view_mode': 'form',
                'context': {'default_employee_id': self.id},
            }
        else:
            # Pas de contrat: en créer un
            return {
                'type': 'ir.actions.act_window',
                'name': _('Nouveau Contrat'),
                'res_model': 'hr.version',
                'view_mode': 'form',
                'context': {'default_employee_id': self.id},
            }

    def action_open_contracts(self):
        """Ouvrir la liste des contrats de l'employé"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Contrats de %s') % self.name,
            'res_model': 'hr.version',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }
