import json
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Champs d'une prestation conservés pour pouvoir l'annuler (la recréer).
SNAPSHOT_FIELDS = ('name', 'employee_id', 'date', 'duration', 'work_entry_type_id', 'dz_origin')


class HrWorkEntry(models.Model):
    _name = 'hr.work.entry'
    _inherit = ['hr.work.entry', 'hr.dz.gantt.employee.mixin']

    # ── Données du Gantt ─────────────────────────────────────────────────────

    @api.model
    def get_gantt_data(self, domain, groupby, read_specification, **kwargs):
        """Les prestations annulées ne sont ni affichées ni comptées."""
        domain = list(domain or []) + [('state', '!=', 'cancelled')]
        return super().get_gantt_data(domain, groupby, read_specification, **kwargs)

    # ── Sélection de journées : analyse ──────────────────────────────────────

    @api.model
    def _dz_gantt_cells(self, cells):
        """[[employee_id, 'AAAA-MM-JJ'], …] → {employé: {dates}} (contrôle d'accès compris)."""
        by_employee = defaultdict(set)
        for employee_id, day in cells or []:
            by_employee[int(employee_id)].add(fields.Date.to_date(day))
        employees = self.env['hr.employee'].browse(list(by_employee)).exists()
        employees.check_access('read')
        return {employee: by_employee[employee.id] for employee in employees}

    @api.model
    def _dz_planned_hours(self, employee, day):
        """Heures prévues au planning du contrat ce jour-là (0 = jour non travaillé)."""
        engine = self.env['hr.dz.work.entry.engine']
        calendar = engine._calendar(employee, employee._get_version(day))
        return engine._planned_hours(employee, calendar, day) if calendar else 0.0

    @api.model
    def _dz_day_hours(self, employee, day):
        """Durée d'une journée saisie à la main : le planning du jour, sinon la
        journée type du contrat, sinon 8 h (jour non travaillé inclus exprès)."""
        hours = self._dz_planned_hours(employee, day)
        if hours:
            return hours
        calendar = self.env['hr.dz.work.entry.engine']._calendar(employee, employee._get_version(day))
        return (calendar and calendar.hours_per_day) or 8.0

    @api.model
    def _dz_is_sensitive(self, entry):
        """Prestation calculée qu'un remplacement ferait perdre et qui pèse sur
        la paie : heures sup., majoration, heures mises en récupération,
        retenue de retard ou de départ."""
        wtype = entry.work_entry_type_id
        return bool(
            (wtype.overtime_rate or 0.0) > 1.0
            or getattr(wtype, 'is_extra_hours', False)
            or getattr(wtype, 'dz_recovery_role', False) in ('premium', 'banked')
            or getattr(wtype, 'is_late_deduction', False)
            or getattr(wtype, 'is_early_deduction', False)
            or wtype.code in ('LATE', 'EARLY'))

    @api.model
    def _dz_gantt_plan(self, cells, include_off_days=False):
        """Ce que ferait un remplacement des journées choisies, sans rien écrire.

        :return: dict(
            todo=[(employé, date, prestations existantes)],
            locked=n (paie validée ou hors contrat),
            off=n (jours non travaillés laissés de côté),
            sensitive=n (journées avec heures sup. / retenues qui seraient perdues),
            replaced=n (journées qui ont déjà des prestations))
        """
        by_employee = self._dz_gantt_cells(cells)
        plan = {'todo': [], 'locked': 0, 'off': 0, 'sensitive': 0, 'replaced': 0}
        if not by_employee:
            return plan
        all_days = sorted({day for days in by_employee.values() for day in days})
        per_cell = defaultdict(lambda: self.browse())
        for entry in self.search([('employee_id', 'in', [e.id for e in by_employee]),
                                  ('date', 'in', all_days), ('state', '!=', 'cancelled')]):
            per_cell[(entry.employee_id.id, entry.date)] |= entry
        for employee, days in by_employee.items():
            # Dates de contrat : réservées aux gestionnaires RH en lecture ;
            # on ne fait ici que vérifier quels jours sont modifiables.
            contract = employee.sudo()
            for day in sorted(days):
                entries = per_cell[(employee.id, day)]
                version = contract._get_version(day)
                in_contract = bool(version and version.contract_date_start) and (
                    version.contract_date_start <= day
                    and (not version.contract_date_end or day <= version.contract_date_end))
                if not in_contract or any(e.state == 'validated' for e in entries):
                    plan['locked'] += 1
                    continue
                if not include_off_days and not self._dz_planned_hours(contract, day):
                    plan['off'] += 1
                    continue
                if entries:
                    plan['replaced'] += 1
                if any(self._dz_is_sensitive(e) for e in entries):
                    plan['sensitive'] += 1
                plan['todo'].append((employee, day, entries))
        return plan

    @api.model
    def dz_gantt_preview(self, cells, include_off_days=False):
        """Résumé d'un remplacement, pour la confirmation côté écran."""
        plan = self._dz_gantt_plan(cells, include_off_days)
        return {key: (len(value) if key == 'todo' else value) for key, value in plan.items()}

    # ── Sélection de journées : actions (journalisées, annulables) ───────────

    @api.model
    def _dz_snapshot(self, entries):
        return [{name: (entry[name].id if name in ('employee_id', 'work_entry_type_id')
                        else fields.Date.to_string(entry[name]) if name == 'date'
                        else entry[name]) for name in SNAPSHOT_FIELDS}
                for entry in entries]

    @api.model
    def dz_gantt_set_type(self, cells, work_entry_type_id, include_off_days=False):
        """Remplace les prestations des journées choisies par une seule
        prestation du type donné (saisie RH : le calcul ne la touche plus).

        - Durée : les heures prévues ce jour-là au planning du contrat (jamais
          la somme des prestations remplacées, qui compterait les heures sup.).
        - Jours non travaillés (repos, planning à 0 h) : laissés de côté, sauf
          `include_off_days` (alors la journée type du contrat).
        - Paie validée ou hors contrat : journée non modifiée.
        - Journalisé dans `hr.dz.work.entry.change` : qui, quoi, et de quoi
          annuler (`dz_gantt_undo`).

        Droits : il faut pouvoir créer et modifier des prestations ; le
        remplacement (suppression comprise, réservée à l'administrateur dans
        Odoo) se fait ensuite avec les droits système, comme le calcul.

        :return: {'done', 'locked', 'off', 'change_id'}
        """
        self.check_access('create')
        self.check_access('write')
        wtype = self.env['hr.work.entry.type'].browse(work_entry_type_id).exists()
        if not wtype:
            raise UserError(_("Type de prestation introuvable."))
        plan = self._dz_gantt_plan(cells, include_off_days)
        to_remove = self.browse()
        to_create = []
        for employee, day, entries in plan['todo']:
            to_remove |= entries
            to_create.append({
                'name': '%s %s' % (wtype.name, day.strftime('%d/%m/%Y')),
                'employee_id': employee.id,
                'date': day,
                'duration': self._dz_day_hours(employee.sudo(), day),
                'work_entry_type_id': wtype.id,
                'dz_origin': 'manual',
            })
        removed = self._dz_snapshot(to_remove)
        to_remove.sudo().unlink()
        created = self.create(to_create)
        change = self.env['hr.dz.work.entry.change'].sudo().create({
            'action': 'set_type',
            'work_entry_type_id': wtype.id,
            'day_count': len(to_create),
            'employee_ids': [(6, 0, list({v['employee_id'] for v in to_create}))],
            'removed_json': json.dumps(removed),
            'created_ids': [(6, 0, created.ids)],
        }) if to_create else self.env['hr.dz.work.entry.change']
        return {'done': len(to_create), 'locked': plan['locked'], 'off': plan['off'],
                'change_id': change.id or False}

    @api.model
    def dz_gantt_reset(self, cells):
        """Rend les journées choisies au calcul automatique : les saisies RH
        sont retirées (journalisé, annulable) et le moteur recalcule depuis les
        pointages. Paie validée ou hors contrat : journée non modifiée.

        :return: {'done', 'locked', 'change_id'}
        """
        self.check_access('write')
        plan = self._dz_gantt_plan(cells, include_off_days=True)
        manual = self.browse()
        days_by_employee = defaultdict(set)
        for employee, day, entries in plan['todo']:
            manual |= entries.filtered(lambda e: e.dz_origin == 'manual')
            days_by_employee[employee].add(day)
        removed = self._dz_snapshot(manual)
        manual.sudo().unlink()
        Engine = self.env['hr.dz.work.entry.engine']
        for employee, days in days_by_employee.items():
            Engine.sync(employee, min(days), max(days))
        change = self.env['hr.dz.work.entry.change'].sudo().create({
            'action': 'reset',
            'day_count': len(plan['todo']),
            'employee_ids': [(6, 0, [e.id for e in days_by_employee])],
            'removed_json': json.dumps(removed),
        }) if removed else self.env['hr.dz.work.entry.change']
        return {'done': len(plan['todo']), 'locked': plan['locked'], 'change_id': change.id or False}

    @api.model
    def dz_gantt_undo(self, change_id):
        """Annule une modification faite depuis le Gantt (voir le journal)."""
        self.check_access('write')
        change = self.env['hr.dz.work.entry.change'].browse(change_id).exists()
        if not change:
            raise UserError(_("Modification introuvable."))
        change.action_undo()
        return True


class HrDzWorkEntryChange(models.Model):
    """Journal des modifications de prestations faites depuis le Gantt : qui a
    remplacé quoi, quand — et de quoi l'annuler."""
    _name = 'hr.dz.work.entry.change'
    _description = 'Modification de prestations (Gantt)'
    _order = 'create_date desc, id desc'
    _rec_name = 'summary'

    action = fields.Selection([
        ('set_type', 'Journées remplacées'),
        ('reset', 'Rendues au calcul automatique'),
    ], string='Action', required=True, readonly=True)
    work_entry_type_id = fields.Many2one('hr.work.entry.type', string='Type posé', readonly=True)
    day_count = fields.Integer(string='Journées', readonly=True)
    employee_ids = fields.Many2many('hr.employee', string='Employés', readonly=True)
    removed_json = fields.Text(string='Prestations retirées', readonly=True,
                               help="Instantané des prestations supprimées, pour l'annulation.")
    created_ids = fields.Many2many('hr.work.entry', string='Prestations créées', readonly=True)
    undone = fields.Boolean(string='Annulée', readonly=True)
    summary = fields.Char(compute='_compute_summary')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, readonly=True)

    @api.depends('action', 'work_entry_type_id', 'day_count', 'employee_ids')
    def _compute_summary(self):
        for change in self:
            what = (_('« %s »', change.work_entry_type_id.name) if change.action == 'set_type'
                    else _('calcul automatique'))
            change.summary = _('%(n)s journée(s) → %(what)s (%(people)s)', n=change.day_count,
                               what=what, people=', '.join(change.employee_ids.mapped('name')))

    def action_undo(self):
        """Retire ce qui a été créé et recrée ce qui a été retiré — tant que la
        paie de ces jours n'est pas validée."""
        WorkEntry = self.env['hr.work.entry'].sudo()
        for change in self:
            if change.undone:
                raise UserError(_("Cette modification est déjà annulée."))
            # Comme le remplacement : droits vérifiés par dz_gantt_undo, puis
            # suppression / recréation avec les droits système.
            created = change.sudo().created_ids.exists()
            if any(e.state == 'validated' for e in created):
                raise UserError(_("La paie de ces jours est validée : annulation impossible."))
            removed = json.loads(change.removed_json or '[]')
            created.unlink()
            WorkEntry.create(removed)
            days = defaultdict(set)
            for vals in removed:
                days[vals['employee_id']].add(fields.Date.to_date(vals['date']))
            for employee_id, dates in days.items():
                self.env['hr.dz.work.entry.engine'].sync(
                    self.env['hr.employee'].browse(employee_id), min(dates), max(dates))
            change.undone = True
