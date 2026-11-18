"""Mixin Gantt RH : quelles lignes d'employés, quels jours grisés."""

from datetime import date, datetime

from odoo.tests import TransactionCase, tagged

START, STOP = datetime(2026, 9, 30, 23, 0), datetime(2026, 10, 31, 23, 0)


@tagged('post_install', '-at_install', 'dz_gantt')
class TestGanttMixin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Model = cls.env['hr.work.entry']   # hérite du mixin
        cls.dept_a = cls.env['hr.department'].create({'name': 'Atelier A'})
        cls.dept_b = cls.env['hr.department'].create({'name': 'Atelier B'})
        cls.in_a = cls._employee('Absent A', cls.dept_a)
        cls.in_b = cls._employee('Absent B', cls.dept_b)

    @classmethod
    def _employee(cls, name, department, company=None):
        employee = cls.env['hr.employee'].create({
            'name': name, 'department_id': department.id,
            'company_id': (company or cls.env.company).id})
        employee.version_id.contract_date_start = date(2026, 1, 1)
        return employee

    def _rows(self, scope_domain):
        groups = []
        extra = self.Model._gantt_extra_groups(scope_domain, ['employee_id'], groups, START, STOP)
        return {g['employee_id'][0] for g in extra}

    def test_every_employee_under_contract_has_a_row(self):
        rows = self._rows([])
        self.assertTrue({self.in_a.id, self.in_b.id} <= rows)

    def test_team_filter_keeps_that_teams_absentees(self):
        """Filtre « mon atelier » : l'absent de l'atelier A reste, celui de B non."""
        rows = self._rows([('employee_id.department_id', '=', self.dept_a.id)])
        self.assertIn(self.in_a.id, rows)
        self.assertNotIn(self.in_b.id, rows)
        rows = self._rows([('department_id', '=', self.dept_b.id)])
        self.assertEqual(rows & {self.in_a.id, self.in_b.id}, {self.in_b.id})

    def test_other_conditions_do_not_hide_absentees(self):
        rows = self._rows([('state', '!=', 'cancelled'), ('work_entry_type_id.code', '=', 'CA')])
        self.assertIn(self.in_a.id, rows)

    def test_or_domain_adds_nothing(self):
        """Un OU ne se traduit pas sûrement : prudence, aucune ligne ajoutée."""
        self.assertFalse(self._rows(['|', ('employee_id', '=', self.in_a.id),
                                     ('work_entry_type_id.code', '=', 'CA')]))

    def test_other_company_not_added(self):
        other = self.env['res.company'].create({'name': 'Autre société'})
        stranger = self._employee('Autre', self.dept_a, company=other)
        # Sociétés actives de l'utilisateur = celles cochées dans l'interface.
        Model = self.Model.with_context(allowed_company_ids=[self.env.company.id])
        extra = Model._gantt_extra_groups([], ['employee_id'], [], START, STOP)
        self.assertNotIn(stranger.id, {g['employee_id'][0] for g in extra})

    def test_public_holiday_is_greyed(self):
        self.env['resource.calendar.leaves'].create({
            'name': 'Fête de test', 'resource_id': False,
            'date_from': datetime(2026, 10, 14, 0, 0), 'date_to': datetime(2026, 10, 14, 23, 0)})
        gaps = self.in_a._dz_gantt_unavailability(START, STOP)[self.in_a.id]
        self.assertTrue(any(g['start'] <= '2026-10-14 08:00:00' and g['stop'] >= '2026-10-14 16:00:00'
                            for g in gaps))
