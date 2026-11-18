# -*- coding: utf-8 -*-
from odoo import models


class HrDzWorkEntryEngine(models.AbstractModel):
    _inherit = 'hr.dz.work.entry.engine'

    def _absence_decided_by_hr(self, employee, policy):
        """Mode manuel : l'absence va dans « Anomalies à traiter »."""
        return policy.absence_mode == 'manual'

    def _missing_punch_decided_by_hr(self, employee, policy):
        """Sortie manquante « à trancher par les RH » : les heures manquantes
        vont dans « Anomalies à traiter »."""
        return policy.missing_punch_rule == 'hr'

    def _after_employee_sync(self, employee, date_from, date_to, today, facts):
        """Après le calcul des prestations, la politique de présence s'applique :

        - absences à traiter alignées sur la réalité (mode manuel) ;
        - sorties manquantes à trancher par les RH (si la politique le demande) ;
        - mode automatique : retards et départs des journées terminées retenus ;
        - une décision dont la prestation a disparu est reconstruite.
        """
        super()._after_employee_sync(employee, date_from, date_to, today, facts)
        Deduction = self.env['hr.attendance.deduction'].sudo()
        policy = employee._dz_attendance_policy()
        Deduction._sync_absences(employee, facts, policy)
        Deduction._sync_missing_punches(employee, facts, policy)
        Deduction._auto_validate_finished_days(employee, date_from, date_to, today)
        lost = Deduction.search([
            ('employee_id', '=', employee.id),
            '|', ('status', 'in', ('validated', 'justified')),
            '&', ('status', '=', 'refused'), ('deduction_type', '=', 'missing'),
            ('date', '>=', date_from), ('date', '<=', date_to),
            ('work_entry_id', '=', False),
        ])
        lost._sync_occurrence_entry()
