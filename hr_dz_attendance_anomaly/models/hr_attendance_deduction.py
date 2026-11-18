# -*- coding: utf-8 -*-
"""
Anomalies à traiter : retard, départ anticipé, absence.

C'est la SEULE file de décisions RH sur la présence. Une ligne est créée pour
chaque écart constaté (au-delà de la tolérance pour un retard ou un départ ; pour
une absence, en mode manuel seulement). Trois décisions :

- Retenir   : l'écart est retenu sur la paie selon la politique ;
- Tolérer   : aucune retenue ;
- Justifier : l'absence (ou l'écart) est requalifiée avec un motif — congé,
  maladie, mission… — c'est-à-dire un type de prestation payé.

Deux réglages de la politique : absences (automatique par défaut : prestation
ABSENT directe, pas de ligne ici) et retards / départs (automatique : retenus
seuls à la fin de la journée).

Ce qu'un retard ou un départ coûte dépend de la politique de présence :

- règle « à chaque écart » : la validation crée une prestation LATE / EARLY de la
  durée prévue par la politique (temps réel, demi-journée, journée) ;
- règle « en cumul » (retards) : la validation ne crée rien à elle seule ; les
  retards validés de la période (mois, trimestre ou année) sont recomptés et chaque franchissement du seuil crée
  une prestation LATE, rattachée au retard qui l'a déclenchée ;
- règle « aucune retenue » : l'écart reste visible, sans prestation.
"""
import logging
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrAttendanceDeduction(models.Model):
    _name        = 'hr.attendance.deduction'
    _description = 'Anomalie de présence à traiter (retard, départ, absence)'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Employé',
        required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id')

    attendance_id = fields.Many2one(
        'hr.attendance', string='Présence',
        ondelete='cascade', index=True)
    date = fields.Date(string='Date', required=True, index=True)

    deduction_type = fields.Selection([
        ('late',    'Retard'),
        ('early',   'Départ anticipé'),
        ('absence', 'Absence'),
        ('missing', 'Sortie manquante'),
    ], string='Type', required=True)

    duration = fields.Float(string='Durée (h)', required=True,
                            help="Retard ou départ : écart constaté. Absence : heures prévues ce jour-là. "
                                 "Sortie manquante : heures prévues non couvertes par un pointage complet.")

    status = fields.Selection([
        ('to_validate', 'À traiter'),
        ('validated',   'Retenue'),
        ('refused',     'Tolérée'),
        ('justified',   'Justifiée'),
    ], string='Décision', default='to_validate', required=True, store=True)

    justification_type_id = fields.Many2one(
        'hr.work.entry.type', string='Motif',
        domain="[('is_deductible', '=', False), ('overtime_rate', '<=', 1.0)]",
        help="Pour justifier : le type de prestation qui remplace l'écart "
             "(congé, maladie, mission, récupération…).")
    note = fields.Char(string='Note')

    validated_by   = fields.Many2one('res.users', string='Validé par',   readonly=True, copy=False)
    validated_date = fields.Datetime(string='Date validation',            readonly=True, copy=False)
    policy_id = fields.Many2one(
        'hr.attendance.policy', string='Politique appliquée', readonly=True, copy=False,
        help="Politique de présence en vigueur au moment de la décision.")

    # Prestation générée (work entry LATE / EARLY) — règle « à chaque écart »
    work_entry_id = fields.Many2one(
        'hr.work.entry', string='Prestation',
        readonly=True, copy=False, ondelete='set null')
    # Prestation de cumul déclenchée par CE retard (franchissement du seuil)
    cumul_work_entry_id = fields.Many2one(
        'hr.work.entry', string='Retenue de cumul',
        readonly=True, copy=False, ondelete='set null',
        help="Ce retard a franchi le seuil de la politique : la retenue du cumul lui est rattachée.")
    retained_hours = fields.Float(
        string='Retenu (h)', compute='_compute_retained_hours',
        help="Heures effectivement retenues sur la paie au titre de cet écart.")

    is_manager = fields.Boolean(compute='_compute_is_manager')

    @api.depends('employee_id')
    def _compute_is_manager(self):
        has_mgr = self.env.user.has_group('hr_attendance.group_hr_attendance_manager')
        has_off = self.env.user.has_group('hr_attendance.group_hr_attendance_officer')
        for ded in self:
            ded.is_manager = has_mgr or (
                has_off and ded.employee_id.attendance_manager_id == self.env.user
            )

    @api.depends('work_entry_id.duration', 'cumul_work_entry_id.duration', 'status')
    def _compute_retained_hours(self):
        for ded in self:
            ded.retained_hours = 0.0 if ded.status != 'validated' else (
                (ded.work_entry_id.duration or 0.0) + (ded.cumul_work_entry_id.duration or 0.0))

    @api.depends('employee_id', 'deduction_type', 'date')
    def _compute_display_name(self):
        types = dict(self._fields['deduction_type'].selection)
        for ded in self:
            ded.display_name = '%s — %s %s' % (
                ded.employee_id.name or '', types.get(ded.deduction_type, ''),
                ded.date.strftime('%d/%m/%Y') if ded.date else '')

    def write(self, vals):
        if 'status' in vals:
            attendances = self.mapped('attendance_id').filtered('id')
            if attendances:
                self.env.add_to_compute(
                    attendances._fields['deduction_status'], attendances)
        return super().write(vals)

    # ── Politique ────────────────────────────────────────────────────────────

    def _policy(self):
        self.ensure_one()
        return self.employee_id._dz_attendance_policy()

    def _rule(self, policy):
        self.ensure_one()
        if self.deduction_type in ('absence', 'missing'):
            return 'per_occurrence'
        return policy.late_rule if self.deduction_type == 'late' else policy.early_rule

    def _compute_sanction_duration(self):
        """Heures retenues pour CET écart pris isolément (règle « à chaque écart »)."""
        self.ensure_one()
        if self.deduction_type in ('absence', 'missing'):
            return self.duration
        policy = self._policy()
        if self._rule(policy) != 'per_occurrence':
            return 0.0
        cost = policy.late_cost if self.deduction_type == 'late' else policy.early_cost
        counted = policy._counted_hours(self.deduction_type, self.duration)
        return policy._cost_hours(cost, counted, self.employee_id, self.date)

    def _work_entry_type(self):
        self.ensure_one()
        Type = self.env['hr.work.entry.type']
        if self.deduction_type in ('absence', 'missing'):
            return Type.search([('code', '=', 'ABSENT')], limit=1)
        if self.deduction_type == 'late':
            return (Type.search([('code', '=', 'LATE')], limit=1)
                    or Type.search([('is_late_deduction', '=', True)], limit=1))
        return (Type.search([('code', '=', 'EARLY')], limit=1)
                or Type.search([('is_early_deduction', '=', True)], limit=1))

    def _new_work_entry(self, duration, name, wtype=None):
        self.ensure_one()
        wtype = wtype or self._work_entry_type()
        if not wtype or duration <= 0:
            return self.env['hr.work.entry']
        return self.env['hr.work.entry'].sudo().create({
            'name': name,
            'employee_id': self.employee_id.id,
            'date': self.date,
            'duration': duration,
            'work_entry_type_id': wtype.id,
            'dz_origin': 'deduction',
        })

    def _decision_entry(self):
        """Prestation qui découle de la décision : (type, heures, libellé) ou None."""
        self.ensure_one()
        day = self.date.strftime('%d/%m/%Y')
        if self.status == 'justified' and self.justification_type_id:
            return (self.justification_type_id,
                    self.duration if self.deduction_type in ('absence', 'missing') else 0.0,
                    '%s %s' % (self.justification_type_id.name, day))
        if self.status == 'refused' and self.deduction_type == 'missing':
            # Tolérée : l'employé est présumé avoir fini sa journée.
            return (self.env['hr.dz.work.entry.engine']._types()['work'], self.duration,
                    _('Présence (sortie non pointée) %s') % day)
        if self.status != 'validated':
            return None
        labels = {'late': _('Retard %s'), 'early': _('Départ anticipé %s'),
                  'absence': _('Absence %s'), 'missing': _('Sortie non pointée %s')}
        return self._work_entry_type(), self._compute_sanction_duration(), labels[self.deduction_type] % day

    def _sync_occurrence_entry(self):
        """Prestation de la décision (créée, ajustée ou retirée). Un retard
        en cumul n'en a pas à lui seul (voir _recompute_late_cumul)."""
        for ded in self:
            entry = ded.work_entry_id
            if entry and entry.state == 'validated':
                continue                                   # paie clôturée : figé
            wanted = ded._decision_entry()
            if wanted and wanted[0] and wanted[1] > 0:
                wtype, duration, label = wanted
                if entry:
                    vals = {}
                    if abs(entry.duration - duration) > 1e-6:
                        vals['duration'] = duration
                    if entry.work_entry_type_id != wtype:
                        vals.update(work_entry_type_id=wtype.id, name=label)
                    if vals:
                        entry.sudo().write(vals)
                else:
                    ded.work_entry_id = ded._new_work_entry(duration, label, wtype)
            elif entry:
                ded.work_entry_id = False
                entry.sudo().unlink()

    @api.model
    def _recompute_late_cumul(self, employee, day):
        """Recompte les retards validés de la période de cumul qui contient `day`
        et reconstruit ses retenues. Période dont une retenue est déjà validée
        (paie clôturée) : figée."""
        policy = employee._dz_attendance_policy()
        start, stop = policy._cumul_bounds(day)
        month = self.sudo().search([
            ('employee_id', '=', employee.id), ('deduction_type', '=', 'late'),
            ('date', '>=', start), ('date', '<=', stop),
        ], order='date, id')
        old = month.mapped('cumul_work_entry_id')
        if any(e.state == 'validated' for e in old):
            return
        month.write({'cumul_work_entry_id': False})
        old.unlink()
        if policy.late_rule != 'cumulative':
            return
        validated = month.filtered(lambda d: d.status == 'validated')
        for ded, hours in policy._cumul_penalties(validated, employee):
            ded.cumul_work_entry_id = ded._new_work_entry(
                hours, _('Cumul de retards au %s') % ded.date.strftime('%d/%m/%Y'))

    def _after_decision(self):
        self._sync_occurrence_entry()
        periods = {(d.employee_id, d.employee_id._dz_attendance_policy()._cumul_bounds(d.date)[0])
                   for d in self if d.deduction_type == 'late'}
        for employee, start in periods:
            self._recompute_late_cumul(employee, start)

    # ── Décisions ────────────────────────────────────────────────────────────

    def action_validate(self):
        """Retient les écarts selon la politique de l'employé."""
        todo = self.filtered(lambda d: d.status == 'to_validate')
        for ded in todo:
            ded.write({
                'status':         'validated',
                'validated_by':   self.env.user.id,
                'validated_date': fields.Datetime.now(),
                'policy_id':      ded._policy().id or False,
            })
        todo._after_decision()

    def action_refuse(self):
        """Tolérer (ou annuler une retenue) : pas de retenue, et le cumul de la
        période est recompté."""
        todo = self.filtered(lambda d: d.status != 'refused')
        todo.write({'status': 'refused', 'policy_id': False,
                    'validated_by': self.env.user.id, 'validated_date': fields.Datetime.now()})
        todo._after_decision()

    def action_justify(self):
        """Justifier avec le motif choisi : l'absence devient une prestation de
        ce type (payée) ; un retard ou un départ justifié n'est pas retenu."""
        missing = self.filtered(lambda d: not d.justification_type_id)
        if missing:
            raise UserError(_("Choisissez d'abord le motif (congé, maladie, mission…)."))
        todo = self.filtered(lambda d: d.status != 'justified')
        todo.write({'status': 'justified', 'policy_id': False,
                    'validated_by': self.env.user.id, 'validated_date': fields.Datetime.now()})
        todo._after_decision()

    def action_reset(self):
        """Revenir sur une décision (tant que la paie du jour n'est pas validée)."""
        frozen = self.filtered(lambda d: d.work_entry_id.state == 'validated')
        if frozen:
            raise UserError(_("La paie de ce jour est validée : la décision ne peut plus changer."))
        todo = self.filtered(lambda d: d.status != 'to_validate')
        todo.write({'status': 'to_validate', 'policy_id': False,
                    'validated_by': False, 'validated_date': False})
        todo._after_decision()

    # ── Absences (mode manuel) ───────────────────────────────────────────────

    @api.model
    def _sync_absences(self, employee, facts, policy):
        """Aligne les absences à traiter sur les faits des journées.

        Mode manuel : chaque journée « absent » a sa ligne à traiter. Si la
        réalité change (pointages arrivés d'une pointeuse hors ligne, congé
        validé après coup), une ligne non tranchée disparaît ; une ligne déjà
        tranchée est remplacée par la réalité — sauf si la paie est validée.
        Mode automatique : le moteur crée directement l'absence, les lignes
        non tranchées sont retirées.
        """
        days = [f['day'] for f in facts]
        if not days:
            return
        existing = self.sudo().search([
            ('employee_id', '=', employee.id), ('deduction_type', '=', 'absence'),
            ('date', '>=', min(days)), ('date', '<=', max(days)),
        ])
        by_day = {}
        for ded in existing:
            by_day.setdefault(ded.date, self.browse())
            by_day[ded.date] |= ded
        manual = policy.absence_mode == 'manual'
        for fact in facts:
            lines = by_day.get(fact['day'], self.browse())
            absent = fact['status'] == 'absent' and fact['in_contract'] and fact['from_attendance']
            if absent and manual:
                pending = lines.filtered(lambda d: d.status == 'to_validate')
                if pending:
                    pending.filtered(lambda d: abs(d.duration - fact['planned']) > 1e-6).write(
                        {'duration': fact['planned']})
                elif not lines:
                    self.sudo().create({
                        'employee_id': employee.id, 'date': fact['day'],
                        'deduction_type': 'absence', 'duration': fact['planned'],
                    })
                continue
            pending = lines.filtered(lambda d: d.status == 'to_validate')
            lines -= pending
            pending.unlink()
            if not absent:
                decided = lines.filtered(lambda d: d.status in ('validated', 'justified')
                                         and d.work_entry_id.state != 'validated')
                if decided:
                    reason = (_('pointages reçus') if fact['attendances'] else
                              _('congé validé') if fact['status'] == 'leave' else
                              _("jour qui n'est plus une absence"))
                    decided.write({'status': 'refused',
                                   'note': _('Remplacée par la réalité : %s') % reason})
                    decided._sync_occurrence_entry()

    @api.model
    def _sync_missing_punches(self, employee, facts, policy):
        """Sorties manquantes « à trancher par les RH » : une ligne par journée
        terminée avec une entrée sans sortie, pour les heures prévues que les
        pointages complets ne couvrent pas.

        La réalité l'emporte : si la sortie est ajoutée (ou la journée change),
        la ligne disparaît avec sa prestation — sauf paie validée.
        """
        days = [f['day'] for f in facts]
        if not days:
            return
        existing = self.sudo().search([
            ('employee_id', '=', employee.id), ('deduction_type', '=', 'missing'),
            ('date', '>=', min(days)), ('date', '<=', max(days)),
        ])
        by_day = {}
        for ded in existing:
            by_day.setdefault(ded.date, self.browse())
            by_day[ded.date] |= ded
        handled = policy.missing_punch_rule == 'hr'
        for fact in facts:
            lines = by_day.get(fact['day'], self.browse())
            missing = 0.0
            if handled and fact['status'] == 'missing_punch' and fact['from_attendance']:
                complete = fact['attendances'].filtered('check_out')
                missing = max(fact['planned'] - sum(complete.mapped('worked_hours')), 0.0)
            if missing > 1e-6:
                if not lines:
                    # Pas de lien vers le pointage : le supprimer ne doit pas
                    # emporter la décision (ni laisser sa prestation orpheline).
                    self.sudo().create({
                        'employee_id': employee.id, 'date': fact['day'],
                        'deduction_type': 'missing', 'duration': missing,
                    })
                    continue
                changed = lines.filtered(lambda d: abs(d.duration - missing) > 1e-6
                                         and d.work_entry_id.state != 'validated')
                if changed:
                    changed.write({'duration': missing})
                    changed.filtered(lambda d: d.status != 'to_validate')._sync_occurrence_entry()
                continue
            gone = lines.filtered(lambda d: d.work_entry_id.state != 'validated')
            entries = gone.mapped('work_entry_id')
            gone.unlink()
            entries.sudo().unlink()

    @api.model
    def _auto_validate_finished_days(self, employee, date_from, date_to, today):
        """Mode automatique : retient les écarts des journées TERMINÉES.

        Pas le jour même : un départ « anticipé » au milieu de la journée peut
        n'être qu'une pause, la journée n'est jugée qu'une fois finie.
        """
        if employee._dz_attendance_policy().mode != 'auto':
            return
        last = min(date_to, today - timedelta(days=1))
        if last < date_from:
            return
        self.sudo().search([
            ('employee_id', '=', employee.id), ('status', '=', 'to_validate'),
            ('deduction_type', 'in', ('late', 'early')),
            ('date', '>=', date_from), ('date', '<=', last),
        ]).with_user(self.env.ref('base.user_root')).action_validate()
