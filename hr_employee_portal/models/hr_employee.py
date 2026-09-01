from odoo import models, fields, api


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    portal_user_state = fields.Selection([
        ('no_portal', 'Pas de portail'),
        ('active',    'Portail actif'),
        ('blocked',   'Bloqué'),
    ], string='État portail', compute='_compute_portal_user_state', store=True)

    @api.depends('user_id', 'user_id.active', 'user_id.share')
    def _compute_portal_user_state(self):
        for employee in self:
            if not employee.user_id:
                employee.portal_user_state = 'no_portal'
            elif employee.user_id.share and employee.user_id.active:
                employee.portal_user_state = 'active'
            else:
                employee.portal_user_state = 'blocked'

    def action_open_portal_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Accès Portail Employé',
            'res_model': 'hr.portal.access.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': self.id,
            },
        }
