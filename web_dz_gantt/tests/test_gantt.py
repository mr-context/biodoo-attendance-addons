"""Vue Gantt : description de vue et données servies au client."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_gantt')
class TestGanttView(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']
        cls.a = cls.Partner.create({'name': 'Gantt A', 'is_company': True})
        cls.b = cls.Partner.create({'name': 'Gantt B', 'is_company': False})

    def _view(self, arch, model='res.partner'):
        return self.env['ir.ui.view'].create({'name': 'gantt test', 'model': model,
                                              'type': 'gantt', 'arch': arch})

    def test_valid_view_exposes_attribute_fields(self):
        view = self._view('<gantt date_start="create_date" date_stop="write_date" '
                          'color="color" default_group_by="is_company"/>')
        result = self.Partner.get_views([(view.id, 'gantt')])
        fields = result['models']['res.partner']['fields']
        for name in ('create_date', 'write_date', 'color', 'is_company'):
            self.assertIn(name, fields)

    def test_dates_are_required(self):
        with self.assertRaises(ValidationError):
            self._view('<gantt date_start="create_date"/>')

    def test_unknown_or_wrong_type_field_refused(self):
        with self.assertRaises(ValidationError):
            self._view('<gantt date_start="create_date" date_stop="nope"/>')
        with self.assertRaises(ValidationError):
            self._view('<gantt date_start="name" date_stop="write_date"/>')

    def test_gantt_data_grouped(self):
        domain = [('id', 'in', (self.a | self.b).ids)]
        data = self.Partner.get_gantt_data(domain, ['is_company'], {'display_name': {}})
        self.assertEqual(data['length'], 2)
        by_value = {g['is_company']: g['__record_ids'] for g in data['groups']}
        self.assertEqual(by_value[True], [self.a.id])
        self.assertEqual(by_value[False], [self.b.id])
        self.assertEqual({r['id'] for r in data['records']}, {self.a.id, self.b.id})
        self.assertEqual(data['unavailabilities'], {})
        self.assertEqual(data['progress_bars'], {})

    def test_gantt_data_without_groupby(self):
        domain = [('id', 'in', (self.a | self.b).ids)]
        data = self.Partner.get_gantt_data(domain, [], {'display_name': {}})
        self.assertEqual(data['length'], 1)
        self.assertEqual(sorted(data['groups'][0]['__record_ids']), sorted((self.a | self.b).ids))

    def test_gantt_data_many2one_group_and_pager(self):
        country = self.env.ref('base.dz')
        self.a.country_id = country
        domain = [('id', 'in', (self.a | self.b).ids)]
        data = self.Partner.get_gantt_data(domain, ['country_id'], {'display_name': {}},
                                           limit=1, offset=0)
        self.assertEqual(data['length'], 2, 'nombre total de groupes, pas la page')
        self.assertEqual(len(data['groups']), 1)
        shown = {rid for g in data['groups'] for rid in g['__record_ids']}
        self.assertEqual({r['id'] for r in data['records']}, shown)

    def test_action_accepts_gantt_mode(self):
        action = self.env['ir.actions.act_window'].create({
            'name': 'Gantt', 'res_model': 'res.partner', 'view_mode': 'gantt,list'})
        self.assertEqual(action.view_mode, 'gantt,list')
