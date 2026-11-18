# -*- coding: utf-8 -*-
"""
Politique de présence : comment l'entreprise traite absences, retards, départs
anticipés et pointages manquants. Tout ce qui varie d'une entreprise à l'autre
est ici, pas dans le code.

Les heures supplémentaires ne sont PAS ici : elles suivent les règles natives
d'Odoo (Présences → Configuration → Règles d'heures supplémentaires : seuils,
taux, horaires de nuit, jours de repos, tolérances, validation), affectées au
contrat. Le moteur de prestations reprend leur taux tel quel.

Chaque société a une politique par défaut ; un employé peut en recevoir une
autre (cadres, atelier, équipe de nuit…). Deux modes d'application :

- automatique : les écarts constatés sont retenus selon la règle dès que la
  journée est terminée ; les RH peuvent toujours corriger ensuite ;
- manuel : chaque écart attend la décision des RH (valider ou refuser).

Retard, deux façons de compter :
- à chaque retard : minutes réelles, demi-journée ou journée entière ;
- en cumul sur le mois de paie : « N retards = X jour(s) » ou
  « N minutes cumulées = X jour(s) » ; seul le franchissement du seuil coûte.
"""

from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

COSTS = [
    ('minutes', 'Temps réel'),
    ('half_day', 'Demi-journée'),
    ('full_day', 'Journée entière'),
]

RECOVERY_MODES = [
    ('paid', 'Payé en heures supplémentaires'),
    ('mixed', 'Mixte : majoration payée, temps rendu en repos'),
    ('recovered', 'Récupéré : tout est rendu en repos'),
]


class HrAttendancePolicy(models.Model):
    _name = 'hr.attendance.policy'
    _description = 'Politique de présence (retenues retards et départs)'
    _order = 'sequence, name'

    name = fields.Char(string='Nom', required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Société', required=True, index=True,
        default=lambda self: self.env.company)

    absence_mode = fields.Selection([
        ('auto', 'Automatique : l\'absence est retenue directement'),
        ('manual', 'Manuel : chaque absence attend la décision des RH'),
    ], string='Traitement des absences', required=True, default='auto',
        help="Automatique : une journée sans pointage devient une absence retenue "
             "(les RH peuvent la corriger).\n"
             "Manuel : elle apparaît dans « Anomalies à traiter » ; les RH la "
             "retiennent, la tolèrent ou la justifient (congé, maladie, mission…).")
    mode = fields.Selection([
        ('manual', 'Manuel : les RH valident chaque retenue'),
        ('auto', 'Automatique : la règle s\'applique seule'),
    ], string='Retards et départs', required=True, default='manual',
        help="Manuel : chaque retard ou départ anticipé attend une décision des RH "
             "(valider ou refuser) avant d'être retenu.\n"
             "Automatique : il est retenu selon la règle dès la fin de la journée. "
             "Les RH peuvent toujours le refuser ensuite, tant que la paie n'est pas validée.")

    # ── Absences et pointages manquants ──────────────────────────────────────
    absence_after_days = fields.Integer(
        string="Constater l'absence après (jours)", default=1,
        help="Une journée sans pointage devient une absence ce nombre de jours après.\n"
             "1 = le lendemain. Mettre 2 ou 3 si des pointeuses restent souvent hors "
             "ligne : l'absence n'apparaît qu'une fois leurs pointages normalement "
             "arrivés (de toute façon, une absence est remplacée par la présence "
             "dès que les pointages arrivent).")
    missing_punch_rule = fields.Selection([
        ('planned', 'Compter la journée prévue (oubli de pointer la sortie)'),
        ('absent', 'Compter une absence si aucun segment n\'est complet'),
        ('ignore', 'Ne compter que le temps pointé complet'),
        ('hr', 'À trancher par les RH (dans « Anomalies à traiter »)'),
    ], string='Entrée sans sortie', required=True, default='planned',
        help="Que faire d'une journée terminée où l'employé a pointé son entrée "
             "mais pas sa sortie.\n"
             "À trancher par les RH : seul le temps pointé complet compte en attendant ; "
             "les heures manquantes vont dans « Anomalies à traiter », où les RH les "
             "retiennent (absence), les tolèrent (journée comptée) ou les justifient.")

    # ── Retards ──────────────────────────────────────────────────────────────
    late_tolerance_minutes = fields.Integer(
        string='Tolérance retard (min)', default=5,
        help="Un retard plus court n'est ni signalé ni retenu.")
    late_rule = fields.Selection([
        ('none', 'Aucune retenue (signalé seulement)'),
        ('per_occurrence', 'À chaque retard'),
        ('cumulative', 'En cumul sur une période'),
    ], string='Retenue des retards', required=True, default='per_occurrence')
    late_cost = fields.Selection(
        COSTS, string='Chaque retard coûte', default='minutes',
        help="Temps réel : on retient exactement la durée du retard.")
    late_rounding_minutes = fields.Integer(
        string='Arrondir chaque retard à (min)', default=0,
        help="Toute tranche commencée est comptée. Exemple : 15 → un retard de "
             "7 min compte 15 min, un retard de 20 min compte 30 min. 0 = minute exacte.")
    late_cumul_period = fields.Selection([
        ('month', 'Mois'),
        ('quarter', 'Trimestre'),
        ('year', 'Année civile'),
    ], string='Période du cumul', default='month',
        help="Le compteur de retards repart à zéro au début de chaque période.")
    late_cumul_basis = fields.Selection([
        ('count', 'Nombre de retards'),
        ('minutes', 'Minutes de retard cumulées'),
    ], string='On compte', default='count')
    late_cumul_threshold = fields.Integer(
        string='Seuil', default=3,
        help="Nombre de retards (ou de minutes cumulées) qui déclenche une retenue. "
             "Exemple : 3 → chaque 3e retard du mois coûte la retenue ci-dessous.")
    late_cumul_days = fields.Float(
        string='Jours retenus par seuil', default=1.0, digits=(4, 2),
        help="Exemple : 1 = une journée, 0,5 = une demi-journée.")

    # ── Départs anticipés ────────────────────────────────────────────────────
    early_tolerance_minutes = fields.Integer(
        string='Tolérance départ anticipé (min)', default=5,
        help="Un départ anticipé plus court n'est ni signalé ni retenu.")
    early_rounding_minutes = fields.Integer(
        string='Arrondir chaque départ à (min)', default=0,
        help="Toute tranche commencée est comptée. 0 = minute exacte.")
    early_rule = fields.Selection([
        ('none', 'Aucune retenue (signalé seulement)'),
        ('per_occurrence', 'À chaque départ anticipé'),
    ], string='Retenue des départs anticipés', required=True, default='per_occurrence')
    early_cost = fields.Selection(COSTS, string='Chaque départ coûte', default='minutes')

    # ── Récupérations ────────────────────────────────────────────────────────
    # Simple par défaut (une case : le jour de repos travaillé ouvre droit à
    # un repos, majoration payée), détaillé pour qui le veut. Chaque heure
    # supplémentaire est soit payée, soit payée en majoration seulement avec
    # son temps rendu en repos (mixte), soit entièrement rendue en repos :
    # jamais payée ET rendue, le moteur ne peut pas compter deux fois.
    recovery_enabled = fields.Boolean(
        string='Accorder des récupérations',
        help="Le travail un jour de repos (et, si vous le réglez, un jour férié ou "
             "des heures supplémentaires) ouvre droit à un repos à prendre plus tard.")
    recovery_rest_day_mode = fields.Selection(
        RECOVERY_MODES, string='Jour de repos travaillé', default='mixed', required=True,
        help="Mixte : la majoration (ex. 50 %) est payée sur la paie, le temps "
             "travaillé est rendu en repos.\n"
             "Récupéré : rien n'est payé, le repos rendu inclut la majoration "
             "(8 h à 150 % = 12 h de repos).\n"
             "Payé : pas de récupération, heures supplémentaires payées.")
    recovery_holiday_mode = fields.Selection(
        RECOVERY_MODES, string='Jour férié travaillé', default='mixed', required=True)
    recovery_overtime_mode = fields.Selection(
        RECOVERY_MODES, string='Heures sup. des jours ouvrés', default='paid', required=True,
        help="Les heures faites au-delà de l'horaire un jour normal. Payées par "
             "défaut ; les RH peuvent toujours changer une heure supplémentaire "
             "précise (fiche de présence, colonne « Compensation »).")
    recovery_overtime_cap = fields.Float(
        string='Payer jusqu\'à (h / mois)', default=0.0,
        help="Les heures sup. des jours ouvrés sont payées jusqu'à ce nombre "
             "d'heures dans le mois ; au-delà, elles suivent le traitement choisi. "
             "0 = le traitement s'applique dès la première heure.")
    recovery_basis = fields.Selection([
        ('worked', 'Le temps réellement travaillé'),
        ('flat', 'Un forfait : journée ou demi-journée'),
    ], string='Repos accordé pour un jour de repos / férié', default='worked', required=True,
        help="Temps réel : 8 h travaillées = 8 h de repos, 4 h = 4 h.\n"
             "Forfait : à partir du seuil, une journée de repos ; en dessous, une "
             "demi-journée.")
    recovery_flat_threshold = fields.Float(
        string='Journée entière à partir de (h)', default=4.0)
    recovery_validity = fields.Selection([
        ('days', 'Dans un délai en jours'),
        ('next_month_end', 'Avant la fin du mois suivant'),
        ('none', 'Sans limite'),
    ], string='À prendre', default='days', required=True)
    recovery_validity_days = fields.Integer(string='Délai (jours)', default=30)
    recovery_expiry_action = fields.Selection([
        ('hr', 'Les RH décident : payer, prolonger ou annuler'),
        ('pay', 'Payer automatiquement le repos non pris'),
        ('lose', 'Le repos non pris est perdu'),
    ], string='Repos non pris à l\'échéance', default='hr', required=True)
    recovery_reminder_days = fields.Integer(
        string='Prévenir les RH (jours avant)', default=7,
        help="Une activité est créée pour le responsable des congés de l'employé "
             "quand un repos arrive à échéance sans avoir été pris. 0 = pas de rappel.")

    # ── Affectation ──────────────────────────────────────────────────────────
    employee_ids = fields.One2many(
        'hr.employee', 'attendance_policy_id', string='Employés affectés',
        help="Employés qui suivent cette politique au lieu de celle de leur société.")
    employee_count = fields.Integer(compute='_compute_employee_count', string='Employés')
    is_company_default = fields.Boolean(
        compute='_compute_is_company_default', string='Par défaut de la société')
    summary = fields.Char(compute='_compute_summary', string='En résumé')

    _late_threshold_positive = models.Constraint(
        'CHECK(late_cumul_threshold > 0)', 'Le seuil du cumul des retards doit être positif.')
    _tolerances_positive = models.Constraint(
        'CHECK(late_tolerance_minutes >= 0 AND early_tolerance_minutes >= 0 '
        'AND late_rounding_minutes >= 0 AND early_rounding_minutes >= 0 '
        'AND absence_after_days >= 1)',
        'Tolérances et arrondis positifs ; une absence se constate au plus tôt le lendemain.')
    _recovery_positive = models.Constraint(
        'CHECK(recovery_overtime_cap >= 0 AND recovery_flat_threshold >= 0 '
        'AND recovery_validity_days >= 0 AND recovery_reminder_days >= 0)',
        'Les réglages des récupérations doivent être positifs.')

    @api.constrains('recovery_validity', 'recovery_validity_days')
    def _check_recovery_validity(self):
        for p in self:
            if p.recovery_validity == 'days' and p.recovery_validity_days < 1:
                raise ValidationError(_("Indiquez en combien de jours une récupération doit être prise."))

    @api.depends('employee_ids')
    def _compute_employee_count(self):
        for policy in self:
            policy.employee_count = len(policy.employee_ids)

    def _compute_is_company_default(self):
        for policy in self:
            policy.is_company_default = policy.company_id.attendance_policy_id == policy

    @api.depends('mode', 'absence_mode', 'late_tolerance_minutes', 'late_rule', 'late_cost',
                 'late_cumul_basis', 'late_cumul_threshold', 'late_cumul_days',
                 'late_cumul_period',
                 'early_tolerance_minutes', 'early_rule', 'early_cost',
                 'recovery_enabled', 'recovery_rest_day_mode')
    def _compute_summary(self):
        costs = dict(COSTS)
        for p in self:
            if p.late_rule == 'none':
                late = _("retards signalés sans retenue")
            elif p.late_rule == 'per_occurrence':
                late = _("chaque retard coûte : %s") % costs[p.late_cost].lower()
            else:
                days = ('%g' % p.late_cumul_days).replace('.', ',')
                unit = _("retards") if p.late_cumul_basis == 'count' else _("min de retard cumulées")
                period = dict(self._fields['late_cumul_period'].selection)[p.late_cumul_period or 'month']
                late = _("%(n)s %(unit)s = %(d)s jour(s) retenu(s), par %(p)s") % {
                    'n': p.late_cumul_threshold, 'unit': unit, 'd': days, 'p': period.lower()}
            early = (_("départs anticipés sans retenue") if p.early_rule == 'none'
                     else _("chaque départ anticipé : %s") % costs[p.early_cost].lower())
            mode = _("automatique") if p.mode == 'auto' else _("validation RH")
            absence = (_("absences retenues automatiquement") if p.absence_mode == 'auto'
                       else _("absences validées par les RH"))
            p.summary = _("%(absence)s ; %(late)s (tolérance %(lt)s min) ; %(early)s (tolérance %(et)s min) — %(mode)s") % {
                'absence': absence, 'late': late, 'lt': p.late_tolerance_minutes, 'early': early,
                'et': p.early_tolerance_minutes, 'mode': mode}
            if p.recovery_enabled:
                p.summary += _(" ; jour de repos travaillé : %s") % (
                    dict(RECOVERY_MODES)[p.recovery_rest_day_mode].split(' :')[0].lower())

    @api.constrains('late_rule', 'late_cumul_days')
    def _check_cumul_days(self):
        for p in self:
            if p.late_rule == 'cumulative' and p.late_cumul_days <= 0:
                raise ValidationError(_("Indiquez combien de jours retient chaque seuil de retards."))

    # ── Calcul des retenues ──────────────────────────────────────────────────

    @api.model
    def _day_hours(self, employee, day):
        """Heures prévues ce jour-là (planning de l'employé) ; à défaut sa
        journée type. Une demi-journée de retenue est la moitié de CE jour."""
        engine = self.env['hr.dz.work.entry.engine']
        version = employee._get_version(day)
        hours = engine._planned_hours(employee, engine._calendar(employee, version), day)
        calendar = engine._calendar(employee, version)
        return hours or (calendar and calendar.hours_per_day) or 8.0

    @staticmethod
    def _round_up(minutes, step):
        """Toute tranche commencée est due : 7 min arrondies à 15 → 15."""
        if not step or minutes <= 0:
            return minutes
        return -(-minutes // step) * step

    def _counted_hours(self, deduction_type, duration):
        """Durée d'un écart après l'arrondi de la politique (heures)."""
        self.ensure_one()
        step = self.late_rounding_minutes if deduction_type == 'late' else self.early_rounding_minutes
        return self._round_up(round(duration * 60.0), step) / 60.0

    def _cumul_bounds(self, day):
        """Période du cumul des retards qui contient `day` : (début, fin)."""
        self.ensure_one()
        period = self.late_cumul_period or 'month'
        if period == 'year':
            return day.replace(month=1, day=1), day.replace(month=12, day=31)
        first_month = day.month if period == 'month' else 3 * ((day.month - 1) // 3) + 1
        start = day.replace(month=first_month, day=1)
        months = 1 if period == 'month' else 3
        year, month = divmod(start.month - 1 + months, 12)
        stop = start.replace(year=start.year + year, month=month + 1, day=1) - timedelta(days=1)
        return start, stop

    def _cost_hours(self, cost, duration, employee, day):
        """Heures retenues pour un écart de `duration` heures (déjà arrondi)."""
        self.ensure_one()
        if cost == 'half_day':
            return self._day_hours(employee, day) / 2.0
        if cost == 'full_day':
            return self._day_hours(employee, day)
        return duration

    def _cumul_penalties(self, deductions, employee):
        """Retards VALIDÉS d'une période, triés → [(retard qui franchit le seuil, heures)].

        Compte en nombre : chaque N-ième retard coûte X jours.
        Compte en minutes : chaque fois que le cumul franchit un multiple de N
        minutes, X jours (un long retard peut franchir plusieurs seuils).
        """
        self.ensure_one()
        threshold = self.late_cumul_threshold
        penalties = []

        def hours(ded):
            return self.late_cumul_days * self._day_hours(employee, ded.date)

        if self.late_cumul_basis == 'count':
            for rank, ded in enumerate(deductions, 1):
                if rank % threshold == 0:
                    penalties.append((ded, hours(ded)))
            return penalties
        total = 0.0
        for ded in deductions:
            before = int(total // threshold)
            total += self._counted_hours('late', ded.duration) * 60.0
            crossed = int(total // threshold) - before
            if crossed:
                penalties.append((ded, hours(ded) * crossed))
        return penalties

    # ── Récupérations ────────────────────────────────────────────────────────

    def _recovery_mode(self, kind):
        """Traitement des heures sup. d'une journée de nature `kind`
        ('rest_day', 'holiday', 'weekday') : 'paid', 'mixed' ou 'recovered'."""
        self.ensure_one()
        if not self.recovery_enabled:
            return 'paid'
        return {
            'rest_day': self.recovery_rest_day_mode,
            'holiday': self.recovery_holiday_mode,
        }.get(kind, self.recovery_overtime_mode) or 'paid'

    def _recovery_expiry(self, day):
        """Date limite pour prendre le repos acquis le jour `day` (False = aucune)."""
        self.ensure_one()
        if self.recovery_validity == 'none':
            return False
        if self.recovery_validity == 'next_month_end':
            first_next = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
            return (first_next + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return day + timedelta(days=self.recovery_validity_days or 30)

    def _recovery_credit(self, employee, day, kind, banked):
        """Heures de repos acquises pour une journée.

        `banked` : [(taux, heures, traitement)] des heures sup. rendues en repos.
        Mixte : le temps travaillé (la majoration est payée à part).
        Récupéré : temps × taux (la majoration est rendue en repos aussi).
        Forfait (jour de repos / férié seulement) : une journée à partir du
        seuil, une demi-journée en dessous.
        """
        self.ensure_one()
        worked = sum(hours for _rate, hours, _t in banked)
        if worked <= 0:
            return 0.0
        if kind in ('rest_day', 'holiday') and self.recovery_basis == 'flat':
            full = self._day_hours(employee, day)
            return full if worked >= self.recovery_flat_threshold else full / 2.0
        return sum(hours * (rate if treatment == 'recovered' else 1.0)
                   for rate, hours, treatment in banked)
