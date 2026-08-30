"""
Extension du modèle hr.work.entry pour l'Algérie.
Ajoute le lien avec les présences et les anomalies.
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
    anomaly_ids = fields.One2many(
        'hr.work.entry.anomaly',
        'work_entry_id',
        string='Anomalies',
    )
    has_anomaly = fields.Boolean(
        compute='_compute_has_anomaly',
        store=True,
    )

    @api.depends('anomaly_ids', 'anomaly_ids.state')
    def _compute_has_anomaly(self):
        for entry in self:
            entry.has_anomaly = bool(entry.anomaly_ids.filtered(lambda a: a.state == 'pending'))

    def action_view_anomalies(self):
        """Voir les anomalies liées"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Anomalies'),
            'res_model': 'hr.work.entry.anomaly',
            'view_mode': 'list,form',
            'domain': [('work_entry_id', '=', self.id)],
            'context': {'default_work_entry_id': self.id},
        }


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

    @api.depends('is_deductible', 'overtime_rate', 'is_leave')
    def _compute_semantic_flags(self):
        for t in self:
            # Jour payé = tout ce qui n'est ni déductible ni des heures supp
            # Les HS ont leur propre règle de calcul → overtime_rate > 1
            t.is_paid = not t.is_deductible and t.overtime_rate <= 1.0
            # Présence physique = payé + pas un congé (transport/panier versés
            # uniquement les jours où l'employé est physiquement présent)
            t.is_standard_work = t.is_paid and not t.is_leave
