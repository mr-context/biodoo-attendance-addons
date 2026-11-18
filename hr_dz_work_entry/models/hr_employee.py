from odoo import models, fields


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    attendance_policy_id = fields.Many2one(
        'hr.attendance.policy', string='Politique de présence',
        groups='hr.group_hr_user', index=True,
        domain="[('company_id', '=', company_id)]",
        help="Laisser vide pour suivre la politique par défaut de la société.")

    dz_work_entry_error = fields.Text(
        string='Erreur de calcul des prestations', readonly=True, copy=False,
        groups='hr.group_hr_user',
        help="Dernière erreur du calcul automatique des prestations de cet employé. "
             "Vide quand le dernier calcul a réussi.")
    dz_work_entry_error_date = fields.Datetime(string='Erreur du', readonly=True, copy=False,
                                               groups='hr.group_hr_user')

    def _dz_attendance_policy(self):
        """Politique qui s'applique : celle de l'employé, sinon celle de sa société."""
        self.ensure_one()
        employee = self.sudo()
        return employee.attendance_policy_id or (
            employee.company_id or self.env.company)._dz_attendance_policy()
