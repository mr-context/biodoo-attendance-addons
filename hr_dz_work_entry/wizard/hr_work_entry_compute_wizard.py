"""
Assistant « Calculer les prestations ».

Il ne calcule rien lui-même : il confie la période au moteur unique
`hr.dz.work.entry.engine` (pointages, fériés, congés, absences, retenues
validées), le même que celui du calcul de nuit et de l'arrivée des pointages.
"""

import logging

from odoo import models, fields, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrWorkEntryComputeWizard(models.TransientModel):
    _name = 'hr.work.entry.compute.wizard'
    _description = 'Calculer les prestations'

    date_from = fields.Date(
        string='Date début',
        required=True,
        default=lambda self: fields.Date.today().replace(day=1),
    )
    date_to = fields.Date(
        string='Date fin',
        required=True,
        default=fields.Date.today,
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        string='Employés',
        help='Laisser vide pour tous les employés dont le contrat est en source « Présences ».',
    )
    force_regenerate = fields.Boolean(
        string='Repartir de zéro',
        default=False,
        help="Supprime d'abord les prestations calculées automatiquement sur la "
             "période, puis les recalcule. Les saisies et corrections des RH, les "
             "retenues validées et les prestations validées ne sont jamais touchées.",
    )

    def action_compute(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_('La date de début doit précéder la date de fin.'))

        engine = self.env['hr.dz.work.entry.engine']
        employees = self.employee_ids or self.env['hr.employee'].search([])
        managed = engine._attendance_employees(employees)
        if not managed:
            raise UserError(_(
                "Aucun employé à calculer : les prestations sont calculées depuis "
                "les pointages pour les contrats en source « Présences (Pointage) ». "
                "Vérifiez la source des prestations sur le contrat."))
        skipped = employees - managed
        if skipped:
            _logger.info("Prestations — %d employé(s) en source « Horaire » ignoré(s) : %s",
                         len(skipped), ', '.join(skipped.mapped('name')))

        if self.force_regenerate:
            self.env['hr.work.entry'].sudo().with_context(active_test=False).search([
                ('employee_id', 'in', managed.ids),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
                ('state', '!=', 'validated'),
                ('dz_origin', 'in', ('auto', False)),
            ]).unlink()

        entries = engine.sync(managed, self.date_from, self.date_to)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Prestations calculées (%d)') % len(entries),
            'res_model': 'hr.work.entry',
            'view_mode': 'list,form',
            'domain': [('employee_id', 'in', managed.ids),
                       ('date', '>=', self.date_from),
                       ('date', '<=', self.date_to)],
            'target': 'current',
        }
