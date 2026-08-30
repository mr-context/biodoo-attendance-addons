# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError


class ZktecoUserMappingWizard(models.TransientModel):
    _name = 'zkteco.user.mapping.wizard'
    _description = 'Mapper un user device vers un employé Odoo'

    device_user_id  = fields.Many2one('zkteco.device.user', required=True, readonly=True)
    pin             = fields.Char(related='device_user_id.pin',            readonly=True)
    name_on_device  = fields.Char(related='device_user_id.name_on_device', readonly=True)
    pending_count   = fields.Integer(related='device_user_id.pending_attlog_count', readonly=True)
    device_name     = fields.Char(related='device_user_id.device_id.display_name', readonly=True)

    # Suggestions calculées au chargement
    suggestion_line_ids = fields.One2many(
        'zkteco.user.mapping.wizard.line', 'wizard_id',
        string='Suggestions', readonly=True)

    # Choix de l'admin
    employee_id  = fields.Many2one('hr.employee', string='Lier à l\'employé')
    new_emp_name = fields.Char(string='Nom (si création)', compute='_compute_new_emp_name')

    @api.depends('device_user_id')
    def _compute_new_emp_name(self):
        for w in self:
            w.new_emp_name = w.device_user_id.name_on_device or ''

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        du_id = self.env.context.get('default_device_user_id')
        if du_id:
            du = self.env['zkteco.device.user'].browse(du_id)
            suggestions = du.get_name_suggestions(limit=5)
            vals['suggestion_line_ids'] = [
                (0, 0, {'score': score, 'employee_id': emp.id})
                for score, emp in suggestions
            ]
        return vals

    # ── actions ───────────────────────────────────────────────────

    def action_map(self):
        self.ensure_one()
        if not self.employee_id:
            raise UserError('Sélectionnez un employé à lier.')
        self.device_user_id._do_map(self.employee_id)
        return {'type': 'ir.actions.act_window_close'}

    def action_create_employee(self):
        self.ensure_one()
        name = self.new_emp_name or self.device_user_id.name_on_device
        if not name:
            raise UserError('Nom requis pour créer un employé.')

        # Dédup : si le PIN est déjà utilisé → lier sans créer
        existing = self.env['hr.employee'].sudo().search(
            [('zkteco_pin', '=', self.device_user_id.pin)], limit=1)
        if existing:
            self.device_user_id._do_map(existing)
        else:
            emp = self.env['hr.employee'].sudo().create({
                'name':       name,
                'zkteco_pin': self.device_user_id.pin,
            })
            self.device_user_id._do_map(emp)

        return {'type': 'ir.actions.act_window_close'}

    def action_ignore(self):
        self.ensure_one()
        self.device_user_id.action_ignore()
        return {'type': 'ir.actions.act_window_close'}

    def action_delete_from_device(self):
        self.ensure_one()
        self.device_user_id.action_delete_from_device()
        return {'type': 'ir.actions.act_window_close'}


class ZktecoUserMappingWizardLine(models.TransientModel):
    _name = 'zkteco.user.mapping.wizard.line'
    _description = 'Suggestion de mapping'
    _order = 'score desc'

    wizard_id   = fields.Many2one('zkteco.user.mapping.wizard', ondelete='cascade')
    score       = fields.Integer(string='Similarité %')
    employee_id = fields.Many2one('hr.employee', string='Employé', readonly=True)
    emp_name    = fields.Char(related='employee_id.name', string='Nom', readonly=True)

    def action_select(self):
        self.ensure_one()
        self.wizard_id.employee_id = self.employee_id