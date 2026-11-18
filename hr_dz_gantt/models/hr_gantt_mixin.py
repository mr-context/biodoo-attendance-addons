from odoo import api, models


class HrDzGanttEmployeeMixin(models.AbstractModel):
    """Gantt RH : à hériter par tout modèle qui a un `employee_id`.

    - jours et heures hors planning grisés (`_gantt_unavailability`) ;
    - une ligne pour chaque employé sous contrat sur la période, même sans
      aucune donnée (`_gantt_extra_groups`) : l'absent est celui qu'on cherche.

    Exemple ::

        class HrLeave(models.Model):
            _name = 'hr.leave'
            _inherit = ['hr.leave', 'hr.dz.gantt.employee.mixin']
    """
    _name = 'hr.dz.gantt.employee.mixin'
    _description = 'Gantt RH : planning et lignes des employés'

    @api.model
    def _gantt_unavailability(self, field, res_ids, start, stop, scale):
        if field != 'employee_id':
            return super()._gantt_unavailability(field, res_ids, start, stop, scale)
        return self.env['hr.employee'].browse(res_ids)._dz_gantt_unavailability(start, stop)

    @api.model
    def _gantt_extra_groups(self, domain, groupby, groups, start, stop):
        if list(groupby or []) != ['employee_id'] or not start or not stop:
            return super()._gantt_extra_groups(domain, groupby, groups, start, stop)
        scope = self._dz_employee_scope(domain)
        if scope is None:
            return []
        shown = {g['employee_id'][0] for g in groups if g.get('employee_id')}
        versions = self.env['hr.version'].search([
            ('employee_id.active', '=', True),
            ('company_id', 'in', self.env.companies.ids),
            ('contract_date_start', '!=', False),
            ('contract_date_start', '<=', stop.date()),
            '|', ('contract_date_end', '=', False), ('contract_date_end', '>=', start.date()),
        ])
        employees = versions.employee_id
        if scope:
            employees = employees.filtered_domain(scope)
        missing = employees.filtered(lambda e: e.id not in shown)
        return [{'employee_id': (e.id, e.display_name), '__record_ids': []} for e in missing]

    @api.model
    def _dz_employee_scope(self, domain):
        """Part « employés » du domaine de recherche, traduite pour hr.employee.

        Un filtre d'équipe (`employee_id.parent_id`, `department_id`, la
        société…) doit garder les absents de CETTE équipe : on l'applique aux
        employés à ajouter. Les autres conditions (type, état, dates…) ne
        concernent pas l'employé et sont ignorées.

        :return: domaine hr.employee ([] = tous), ou None si le domaine ne se
                 traduit pas sûrement (OU / NON) : alors aucune ligne n'est ajoutée.
        """
        scope = []
        for leaf in domain or []:
            if isinstance(leaf, str):
                if leaf == '&':
                    continue
                return None                               # '|' ou '!' : prudence
            if not (isinstance(leaf, (list, tuple)) and len(leaf) == 3 and isinstance(leaf[0], str)):
                continue
            path, operator, value = leaf
            head, _sep, rest = path.partition('.')
            if head == 'employee_id':
                scope.append((rest or 'id', operator, value))
            elif head in ('department_id', 'company_id') and head in self.env['hr.employee']._fields:
                scope.append((path, operator, value))
            elif head in ('user_id', 'manager_id'):
                return None                               # sens propre au modèle
        return scope
