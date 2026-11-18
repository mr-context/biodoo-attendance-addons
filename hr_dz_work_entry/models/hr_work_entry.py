"""
Extension du modèle hr.work.entry pour l'Algérie.
Ajoute le lien avec les présences et l'origine de chaque prestation
(calcul automatique, retenue validée, saisie RH).
"""

from odoo import models, fields, api, _


class HrWorkEntry(models.Model):
    _inherit = 'hr.work.entry'

    attendance_ids = fields.Many2many(
        'hr.attendance',
        'hr_attendance_work_entry_rel',
        'work_entry_id',
        'attendance_id',
        string='Présences liées',
    )
    # Qui est maître de la prestation. Le moteur de calcul ne touche qu'aux
    # siennes ('auto') ; une prestation créée ou corrigée par les RH devient
    # 'manual' et n'est plus jamais écrasée. Vide = antérieure au moteur.
    dz_origin = fields.Selection([
        ('auto', 'Calcul automatique'),
        ('deduction', 'Retenue validée'),
        ('manual', 'Saisie RH'),
    ], string='Origine', readonly=True, copy=False, index=True)

    # Champs dont la modification par un utilisateur vaut décision RH.
    _DZ_HR_FIELDS = frozenset({'duration', 'work_entry_type_id', 'date', 'employee_id', 'active'})

    @api.model_create_multi
    def create(self, vals_list):
        from_engine = self.env.context.get('dz_work_entry_engine')
        for vals in vals_list:
            vals.setdefault('dz_origin', 'auto' if from_engine else 'manual')
        return super().create(vals_list)

    def write(self, vals):
        if (not self.env.context.get('dz_work_entry_engine')
                and 'dz_origin' not in vals
                and self._DZ_HR_FIELDS.intersection(vals)):
            touched = self.filtered(lambda e: e.dz_origin in ('auto', False))
            if touched:
                super(HrWorkEntry, touched).write({'dz_origin': 'manual'})
        return super().write(vals)


class HrWorkEntryType(models.Model):
    _inherit = 'hr.work.entry.type'

    # ── Flags existants ────────────────────────────────────────────────────
    is_deductible = fields.Boolean(
        string='Déductible',
        default=False,
        help='Déduire ces heures du salaire de base',
    )
    overtime_rate = fields.Float(
        string='Taux HS',
        default=1.0,
        help='Multiplicateur pour heures supplémentaires (1.5 = 150%)',
    )

    # ── Flags calculés automatiquement ────────────────────────────────────
    # Ces flags sont DÉRIVÉS des champs existants — zéro configuration manuelle.
    # Tout nouveau type de prestation hérite automatiquement du bon comportement.

    is_paid = fields.Boolean(
        string='Jour payé',
        compute='_compute_semantic_flags',
        help='Calculé automatiquement.\n'
             'True si le type n\'est pas déductible ET n\'est pas des HS.\n'
             'Exemples payés : travail normal, fériés, CA, maladie, récup.\n'
             'Exemples non payés : absence, CSS, retard, départ anticipé.',
    )
    is_standard_work = fields.Boolean(
        string='Présence physique',
        compute='_compute_semantic_flags',
        help='Calculé automatiquement.\n'
             'True pour les heures de pointage réel (ni congé, ni HS, ni déductible).\n'
             'Utilisé pour les indemnités transport/panier (versées uniquement\n'
             'les jours de présence physique, pas les jours de congé).',
    )

    # ── Flags manuels — identification du rôle dans le wizard ─────────────
    is_late_deduction = fields.Boolean(
        string='Type Retard',
        default=False,
        help='Identifie le type utilisé pour les prestations de retard.\n'
             'Le wizard lit ce flag pour affecter le bon type lors de la\n'
             'création des prestations depuis les déductions validées.',
    )
    is_early_deduction = fields.Boolean(
        string='Type Départ anticipé',
        default=False,
        help='Identifie le type utilisé pour les prestations de départ anticipé.\n'
             'Le wizard lit ce flag pour affecter le bon type lors de la\n'
             'création des prestations depuis les déductions validées.',
    )

    # Rôle dans les récupérations (vide = aucun).
    dz_recovery_role = fields.Selection([
        ('premium', 'Majoration seule (temps rendu en repos)'),
        ('banked', 'Heures mises en récupération'),
        ('taken', 'Récupération prise'),
    ], string='Récupération', index=True,
        help="Majoration seule : heures sup. dont seule la majoration est payée "
             "(ex. 50 %), le temps étant rendu en repos.\n"
             "Heures mises en récupération : heures ni payées ni retenues, rendues en repos.\n"
             "Récupération prise : jour de repos payé, ni absence ni congé annuel.")

    @api.depends('is_deductible', 'overtime_rate', 'is_leave', 'dz_recovery_role')
    def _compute_semantic_flags(self):
        for t in self:
            # Jour payé = tout ce qui n'est ni déductible ni des heures supp
            # Les HS ont leur propre règle de calcul → overtime_rate > 1.
            # Heures mises en récupération : payées plus tard, en repos.
            t.is_paid = (not t.is_deductible and t.overtime_rate <= 1.0
                         and t.dz_recovery_role != 'banked')
            # Présence physique = payé + pas un congé (transport/panier versés
            # uniquement les jours où l'employé est physiquement présent)
            t.is_standard_work = t.is_paid and not t.is_leave
