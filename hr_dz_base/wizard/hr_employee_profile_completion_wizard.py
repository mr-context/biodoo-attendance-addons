
from odoo import models, fields


class HrEmployeeProfileCompletionWizard(models.TransientModel):
    _name = 'hr.employee.profile.completion.wizard'
    _description = "Détail de la complétion du profil employé"

    employee_id = fields.Many2one('hr.employee', required=True)
    completion_percent = fields.Integer(related='employee_id.profile_completion', string="Complétion")
    line_ids = fields.One2many(
        'hr.employee.profile.completion.wizard.line', 'wizard_id', string="Champs à compléter",
    )


class HrEmployeeProfileCompletionWizardLine(models.TransientModel):
    _name = 'hr.employee.profile.completion.wizard.line'
    _description = "Champ manquant pour la complétion du profil employé"
    _order = 'tab, name'

    wizard_id = fields.Many2one('hr.employee.profile.completion.wizard', required=True, ondelete='cascade')
    name = fields.Char(string="Champ")
    tab = fields.Char(string="Onglet")