# -*- coding: utf-8 -*-
"""
Droits à récupération : un repos gagné en travaillant un jour de repos, un
jour férié ou en heures supplémentaires (selon la politique de présence).

Chaque droit est adossé à une attribution de congé « Récupération » d'Odoo,
dont la date de fin est la date limite : le solde, les demandes, la
validation et le calendrier sont ceux des congés. Ce modèle garde ce qu'Odoo
ne sait pas : d'où vient le droit (quel jour, combien d'heures, payé en
mixte ou entièrement récupéré) et ce qu'il devient à l'échéance (pris,
payé, perdu).

Le moteur de prestations crée et corrige les droits à partir des pointages,
comme les prestations : un pointage arrivé en retard ouvre son droit, un
pointage corrigé le recalcule. Un repos déjà pris n'est jamais effacé : si
son droit diminue, le droit est signalé aux RH.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

KINDS = [
    ('rest_day', 'Jour de repos travaillé'),
    ('holiday', 'Jour férié travaillé'),
    ('weekday', 'Heures sup.'),
]


class HrDzRecovery(models.Model):
    _name = 'hr.dz.recovery'
    _description = 'Droit à récupération'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    employee_id = fields.Many2one(
        'hr.employee', string='Employé', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(
        related='employee_id.company_id', store=True, index=True, string='Société')
    department_id = fields.Many2one(
        related='employee_id.department_id', store=True, string='Service')
    date = fields.Date(string='Jour travaillé', required=True, index=True)
    kind = fields.Selection(KINDS, string='Motif', required=True)
    treatment = fields.Selection([
        ('mixed', 'Mixte : majoration payée, temps en repos'),
        ('recovered', 'Récupéré : tout en repos'),
    ], string='Traitement', required=True)
    rate = fields.Float(string='Taux des heures', digits=(5, 2),
                        help="Taux des heures sup. (1,5 = 150 %).")
    worked_hours = fields.Float(string='Heures travaillées', digits=(6, 2))
    hours = fields.Float(string='Repos acquis (h)', digits=(6, 2), tracking=True)
    days = fields.Float(string='Repos acquis (j)', compute='_compute_balance', digits=(6, 2))
    expiry_date = fields.Date(string='À prendre avant le', tracking=True, index=True)
    allocation_id = fields.Many2one(
        'hr.leave.allocation', string='Attribution', readonly=True, ondelete='set null')
    taken_hours = fields.Float(string='Pris (h)', compute='_compute_balance', digits=(6, 2))
    remaining_hours = fields.Float(string='Reste (h)', compute='_compute_balance', digits=(6, 2))
    state = fields.Selection([
        ('active', 'En cours'),
        ('used', 'Pris'),
        ('to_decide', 'Échu : à décider'),
        ('paid', 'Payé'),
        ('lost', 'Perdu'),
        ('cancelled', 'Annulé'),
    ], string='État', default='active', required=True, index=True, tracking=True)
    settled_hours = fields.Float(
        string='Heures réglées', digits=(6, 2),
        help="Repos non pris à l'échéance, payé ou perdu.")
    settled_date = fields.Date(string='Réglé le', index=True,
                               help="Date du paiement (mois de paie) ou de la perte.")
    needs_review = fields.Boolean(
        string='À vérifier', index=True,
        help="Le droit a diminué (pointage corrigé) alors que le repos est déjà "
             "pris en partie : les RH doivent régulariser.")
    reminded = fields.Boolean(string='Rappel envoyé', copy=False)
    note = fields.Text(string='Note')

    _employee_day_uniq = models.UniqueIndex(
        '(employee_id, date) WHERE state != \'cancelled\'',
        'Un seul droit à récupération par employé et par jour.')

    @api.depends('employee_id', 'date', 'kind')
    def _compute_display_name(self):
        kinds = dict(KINDS)
        for rec in self:
            rec.display_name = '%s — %s %s' % (
                rec.employee_id.name or '', kinds.get(rec.kind, ''),
                rec.date.strftime('%d/%m/%Y') if rec.date else '')

    @api.depends('hours', 'allocation_id', 'allocation_id.leaves_taken', 'state', 'settled_hours')
    def _compute_balance(self):
        for rec in self:
            hpd = rec._hours_per_day()
            rec.days = rec.hours / hpd if hpd else 0.0
            taken = rec.allocation_id.leaves_taken if rec.allocation_id.state == 'validate' else 0.0
            rec.taken_hours = min(taken, rec.hours)
            rec.remaining_hours = (0.0 if rec.state in ('paid', 'lost', 'cancelled', 'used')
                                   else max(0.0, rec.hours - rec.taken_hours))

    def _hours_per_day(self):
        self.ensure_one()
        if not self.employee_id:
            return 8.0
        return self.employee_id._get_hours_per_day(self.date) or 8.0

    # ── Attribution de congé ─────────────────────────────────────────────────

    @api.model
    def _leave_type(self):
        return self.env.ref('hr_dz_work_entry.leave_type_recovery', raise_if_not_found=False)

    def _allocation_vals(self):
        self.ensure_one()
        return {
            'name': _('Récupération — %s') % dict(KINDS)[self.kind].lower()
                    + ' ' + self.date.strftime('%d/%m/%Y'),
            'holiday_status_id': self._leave_type().id,
            'employee_id': self.employee_id.id,
            'allocation_type': 'regular',
            'date_from': self.date,
            'date_to': self.expiry_date or False,
            'number_of_days': self.hours / self._hours_per_day(),
        }

    def _ensure_allocation(self):
        """Crée ou ajuste l'attribution pour qu'elle porte `hours` heures."""
        Allocation = self.env['hr.leave.allocation'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        for rec in self.sudo():
            if not rec._leave_type():
                continue
            alloc = rec.allocation_id
            if not alloc or alloc.state == 'refuse':
                alloc = Allocation.create(rec._allocation_vals())
                if alloc.state != 'validate':
                    alloc.action_approve()
                rec.allocation_id = alloc
                continue
            vals = {}
            days = rec.hours / rec._hours_per_day()
            if abs(alloc.number_of_days - days) > 1e-6:
                vals['number_of_days'] = days
            if alloc.date_to != (rec.expiry_date or False):
                vals['date_to'] = rec.expiry_date or False
            if vals:
                alloc.with_context(mail_notrack=True).write(vals)

    def _close_allocation(self):
        """Plus aucun repos ne peut être pris sur ce droit : l'attribution est
        ramenée à ce qui a été pris (ou refusée si rien n'a été pris)."""
        for rec in self.sudo():
            alloc = rec.allocation_id
            if not alloc or alloc.state != 'validate':
                continue
            taken = rec.taken_hours
            if taken > 0:
                alloc.write({'number_of_days': taken / rec._hours_per_day()})
            else:
                alloc.action_refuse()

    # ── Synchronisation depuis le moteur ─────────────────────────────────────

    @api.model
    def _sync_days(self, employee, policy, recovery_days):
        """Aligne les droits sur les journées recalculées par le moteur.

        `recovery_days` : {jour: {'kind', 'banked', 'worked', 'hours'}}.
        Les droits réglés (payés, perdus) sont figés.
        """
        if not recovery_days:
            return
        days = sorted(recovery_days)
        existing = {rec.date: rec for rec in self.sudo().search([
            ('employee_id', '=', employee.id),
            ('date', '>=', days[0]), ('date', '<=', days[-1]),
            ('state', '!=', 'cancelled'),
        ])}
        for day in days:
            info = recovery_days[day]
            rec = existing.get(day)
            if rec and rec.state in ('paid', 'lost'):
                continue
            hours = round(info['hours'], 4)
            if hours <= 0:
                if rec:
                    rec._withdraw(_("Plus d'heures mises en récupération ce jour-là "
                                    "(pointages ou heures sup. modifiés)."))
                continue
            treatments = {t for _r, _h, t in info['banked']}
            vals = {
                'kind': info['kind'],
                'treatment': 'recovered' if treatments == {'recovered'} else 'mixed',
                'rate': max(rate for rate, _h, _t in info['banked']),
                'worked_hours': info['worked'],
                'hours': hours,
            }
            if not rec:
                vals.update(employee_id=employee.id, date=day,
                            expiry_date=policy._recovery_expiry(day))
                rec = self.sudo().create(vals)
                rec._ensure_allocation()
                continue
            changed = {k: v for k, v in vals.items()
                       if (abs(rec[k] - v) > 1e-6 if isinstance(v, float) else rec[k] != v)}
            if not changed:
                continue
            if hours < rec.taken_hours - 1e-6:
                changed['hours'] = rec.taken_hours
                changed['needs_review'] = True
                rec.message_post(body=_(
                    "Le droit est passé à %(new).2f h alors que %(taken).2f h de repos "
                    "sont déjà pris : à régulariser.", new=hours, taken=rec.taken_hours))
            rec.write(changed)
            if rec.state in ('active', 'to_decide', 'used'):
                rec._ensure_allocation()

    def _withdraw(self, reason):
        """Le droit n'existe plus : annulé s'il n'a pas servi, sinon signalé."""
        for rec in self:
            if rec.taken_hours > 0:
                rec.write({'hours': rec.taken_hours, 'needs_review': True})
                rec._ensure_allocation()
                rec.message_post(body=_("%s Repos déjà pris : à régulariser.") % reason)
                continue
            alloc = rec.allocation_id
            rec.write({'state': 'cancelled', 'note': reason, 'allocation_id': False})
            if alloc:
                alloc.sudo().with_context(allocation_skip_state_check=True).unlink()

    # ── Échéances ────────────────────────────────────────────────────────────

    @api.model
    def _cron_expiry(self, today=None):
        """Rappels avant échéance, puis repos non pris traités selon la politique."""
        today = today or fields.Date.context_today(self)
        active = self.search([('state', '=', 'active')])
        for rec in active:
            policy = rec.employee_id._dz_attendance_policy()
            if rec.remaining_hours <= 0:
                if rec.hours > 0 and rec.taken_hours >= rec.hours - 1e-6:
                    rec.state = 'used'
                continue
            if not rec.expiry_date:
                continue
            if rec.expiry_date < today:
                rec._on_expiry(policy, today)
            elif (not rec.reminded and policy.recovery_reminder_days
                  and rec.expiry_date - timedelta(days=policy.recovery_reminder_days) <= today):
                rec._remind()

    def _on_expiry(self, policy, today):
        self.ensure_one()
        action = policy.recovery_expiry_action
        if action == 'pay':
            self._settle('paid', today, _("Payé automatiquement à l'échéance (politique)."))
        elif action == 'lose':
            self._settle('lost', today, _("Perdu à l'échéance (politique)."))
        else:
            self.state = 'to_decide'
            self._remind(expired=True)

    def _remind(self, expired=False):
        self.ensure_one()
        user = (self.employee_id.leave_manager_id
                or self.employee_id.parent_id.user_id)
        if user:
            summary = (_("Récupération échue : payer, prolonger ou annuler") if expired
                       else _("Récupération bientôt échue"))
            self.activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id, summary=summary,
                date_deadline=self.expiry_date or fields.Date.context_today(self),
                note=_("%(emp)s : %(h).2f h de repos non pris, à prendre avant le %(d)s.",
                       emp=self.employee_id.name, h=self.remaining_hours,
                       d=self.expiry_date.strftime('%d/%m/%Y') if self.expiry_date else '—'))
        self.reminded = True

    def _settle(self, state, on_date, note):
        """Règle le repos non pris : payé sur la paie du mois de `on_date`, ou perdu."""
        for rec in self:
            remaining = rec.remaining_hours
            rec._close_allocation()
            rec.write({'state': state, 'settled_hours': remaining,
                       'settled_date': on_date, 'note': note})
            rec.activity_unlink(['mail.mail_activity_data_todo'])

    # ── Décisions RH ─────────────────────────────────────────────────────────

    def _check_open(self):
        for rec in self:
            if rec.state not in ('active', 'to_decide'):
                raise UserError(_("Ce droit est déjà réglé (%s).")
                                % dict(self._fields['state'].selection)[rec.state])

    def action_pay(self):
        """Payer le repos non pris sur la paie du mois en cours."""
        self._check_open()
        self._settle('paid', fields.Date.context_today(self),
                     _("Payé sur décision de %s.") % self.env.user.name)

    def action_lose(self):
        """Annuler le repos non pris (perdu)."""
        self._check_open()
        self._settle('lost', fields.Date.context_today(self),
                     _("Annulé sur décision de %s.") % self.env.user.name)

    def action_extend(self):
        """Repousser la date limite d'un délai de la politique."""
        self._check_open()
        today = fields.Date.context_today(self)
        for rec in self:
            policy = rec.employee_id._dz_attendance_policy()
            start = max(today, rec.expiry_date or today)
            rec.write({'expiry_date': policy._recovery_expiry(start) or False,
                       'state': 'active', 'reminded': False})
            rec._ensure_allocation()
            rec.activity_unlink(['mail.mail_activity_data_todo'])

    def action_mark_reviewed(self):
        self.write({'needs_review': False})

    def action_view_allocation(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'hr.leave.allocation',
            'res_id': self.allocation_id.id, 'view_mode': 'form',
        }

    # ── Lecture (bulletin, fiche employé) ────────────────────────────────────

    @api.model
    def balance_hours(self, employee, on_date):
        """Solde de repos de l'employé à une date : attributions Récupération
        validées commencées − repos pris (validés) − repos réglés à l'échéance.
        Tient compte des attributions saisies à la main par les RH."""
        leave_type = self._leave_type()
        if not leave_type:
            return 0.0
        allocs = self.env['hr.leave.allocation'].sudo().search([
            ('employee_id', '=', employee.id), ('holiday_status_id', '=', leave_type.id),
            ('state', '=', 'validate'), ('date_from', '<=', on_date)])
        granted = sum(allocs.mapped('number_of_hours_display'))
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id', '=', employee.id), ('holiday_status_id', '=', leave_type.id),
            ('state', '=', 'validate'), ('request_date_from', '<=', on_date)])
        taken = sum(leaves.mapped('number_of_hours'))
        # Un droit réglé a son attribution ramenée au pris (ou refusée). S'il a
        # été réglé APRÈS `on_date`, son repos était encore dû à cette date.
        settled_later = sum(self.sudo().search([
            ('employee_id', '=', employee.id), ('state', 'in', ('paid', 'lost')),
            ('date', '<=', on_date), ('settled_date', '>', on_date),
        ]).mapped('settled_hours'))
        return max(0.0, granted + settled_later - taken)

    @api.model
    def portal_summary(self, employee, today=None):
        """Ce que l'employé voit sur son portail : solde, et le prochain repos
        à prendre avant échéance. None si l'employé n'a jamais eu de droit et
        que sa politique n'en accorde pas (pas de carte vide)."""
        today = today or fields.Date.context_today(self)
        rights = self.sudo().search([('employee_id', '=', employee.id),
                                     ('state', '!=', 'cancelled')])
        if not rights and not employee.sudo()._dz_attendance_policy().recovery_enabled:
            return None
        hours = self.balance_hours(employee, today)
        per_day = employee.sudo()._get_hours_per_day(today) or 8.0
        nxt = rights.filtered(lambda r: r.state in ('active', 'to_decide') and r.remaining_hours > 0
                              and r.expiry_date).sorted('expiry_date')[:1]
        return {
            'hours': hours,
            'days': hours / per_day,
            'next_hours': nxt.remaining_hours if nxt else 0.0,
            'next_expiry': nxt.expiry_date if nxt else False,
        }
