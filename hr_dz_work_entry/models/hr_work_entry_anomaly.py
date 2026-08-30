"""
Modèle pour la gestion des anomalies de présence.

Une anomalie est créée quand il y a un écart entre:
- Le temps théorique (calendrier/shift)
- Le temps réel (pointages)
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrWorkEntryAnomaly(models.Model):
    _name = 'hr.work.entry.anomaly'
    _description = 'Anomalie de prestation'
    _order = 'date desc, employee_id'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Description',
        compute='_compute_name',
        store=True,
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Employé',
        required=True,
        index=True,
    )
    date = fields.Date(
        string='Date',
        required=True,
        index=True,
    )
    anomaly_type = fields.Selection([
        ('absent', 'Absence'),
        ('late', 'Retard'),
        ('early_leave', 'Départ anticipé'),
        ('overtime', 'Heures supplémentaires'),
        ('missing_checkout', 'Sortie manquante'),
        ('missing_checkin', 'Entrée manquante'),
        ('incomplete', 'Journée incomplète'),
    ], string='Type d\'anomalie', required=True, tracking=True)

    # Temps théorique vs réel
    theoretical_hours = fields.Float(
        string='Heures théoriques',
        help='Heures prévues selon le calendrier/shift',
    )
    actual_hours = fields.Float(
        string='Heures réelles',
        help='Heures pointées',
    )
    difference_hours = fields.Float(
        string='Écart (heures)',
        compute='_compute_difference',
        store=True,
    )

    # Détails
    theoretical_start = fields.Datetime(string='Début théorique')
    theoretical_end = fields.Datetime(string='Fin théorique')
    actual_start = fields.Datetime(string='Début réel')
    actual_end = fields.Datetime(string='Fin réelle')
    late_minutes = fields.Integer(string='Retard (min)')
    early_leave_minutes = fields.Integer(string='Départ anticipé (min)')

    # Résolution
    state = fields.Selection([
        ('pending', 'À traiter'),
        ('justified', 'Justifiée'),
        ('deducted', 'Déduite'),
        ('tolerated', 'Tolérée'),
        ('validated', 'Validée'),
    ], string='État', default='pending', tracking=True, index=True)

    resolution_type = fields.Selection([
        ('leave_paid', 'Congé payé'),
        ('leave_unpaid', 'Congé sans solde'),
        ('sick', 'Maladie'),
        ('justified', 'Absence justifiée'),
        ('deduct', 'Déduire du salaire'),
        ('tolerate', 'Tolérer'),
        ('overtime_paid', 'HS payées'),
        ('overtime_recup', 'HS en récupération'),
        ('overtime_refused', 'HS refusées'),
    ], string='Résolution')

    work_entry_id = fields.Many2one(
        'hr.work.entry',
        string='Prestation liée',
        help='Prestation créée/modifiée suite à la résolution',
    )
    leave_id = fields.Many2one(
        'hr.leave',
        string='Congé lié',
        help='Congé créé pour justifier l\'absence',
    )
    notes = fields.Text(string='Notes RH')
    resolved_by = fields.Many2one('res.users', string='Résolu par', readonly=True)
    resolved_date = fields.Datetime(string='Date résolution', readonly=True)

    company_id = fields.Many2one(
        'res.company',
        string='Société',
        related='employee_id.company_id',
        store=True,
    )

    @api.depends('employee_id', 'date', 'anomaly_type')
    def _compute_name(self):
        type_labels = dict(self._fields['anomaly_type'].selection)
        for anomaly in self:
            if anomaly.employee_id and anomaly.date and anomaly.anomaly_type:
                anomaly.name = f"{anomaly.employee_id.name} - {anomaly.date} - {type_labels.get(anomaly.anomaly_type, '')}"
            else:
                anomaly.name = _('Nouvelle anomalie')

    @api.depends('theoretical_hours', 'actual_hours')
    def _compute_difference(self):
        for anomaly in self:
            anomaly.difference_hours = anomaly.actual_hours - anomaly.theoretical_hours

    def action_justify_leave_paid(self):
        """Justifier par un congé payé"""
        self._resolve('justified', 'leave_paid')

    def action_justify_leave_unpaid(self):
        """Justifier par un congé sans solde"""
        self._resolve('justified', 'leave_unpaid')

    def action_justify_sick(self):
        """Justifier par maladie"""
        self._resolve('justified', 'sick')

    def action_deduct(self):
        """Déduire du salaire"""
        self._resolve('deducted', 'deduct')

    def action_tolerate(self):
        """Tolérer (pas de déduction)"""
        self._resolve('tolerated', 'tolerate')

    def action_validate_overtime(self):
        """Valider les heures supplémentaires"""
        self._resolve('validated', 'overtime_paid')

    def action_refuse_overtime(self):
        """Refuser les heures supplémentaires"""
        self._resolve('validated', 'overtime_refused')

    def _resolve(self, state, resolution_type):
        """Résoudre l'anomalie et créer/modifier les prestations"""
        for anomaly in self:
            if anomaly.state != 'pending':
                raise UserError(_('Cette anomalie a déjà été traitée.'))

            vals = {
                'state': state,
                'resolution_type': resolution_type,
                'resolved_by': self.env.user.id,
                'resolved_date': fields.Datetime.now(),
            }

            # Créer/modifier la prestation selon le type de résolution
            work_entry = anomaly._create_or_update_work_entry(resolution_type)
            if work_entry:
                vals['work_entry_id'] = work_entry.id

            anomaly.write(vals)

    def _create_or_update_work_entry(self, resolution_type):
        """Créer ou mettre à jour la prestation selon la résolution"""
        self.ensure_one()
        WorkEntry = self.env['hr.work.entry']
        WorkEntryType = self.env['hr.work.entry.type']

        # Déterminer le type de prestation selon la résolution
        type_mapping = {
            'leave_paid': 'LEAVE100',
            'leave_unpaid': 'LEAVE90',
            'sick': 'LEAVE110',
            'deduct': 'ABSENT',
            'tolerate': 'WORK100',
            'overtime_paid': 'OVERTIME',
            'overtime_recup': 'OVERTIME_RECUP',
            'overtime_refused': None,  # Pas de prestation
        }

        code = type_mapping.get(resolution_type)
        if not code:
            return False

        work_entry_type = WorkEntryType.search([('code', '=', code)], limit=1)
        if not work_entry_type:
            return False

        # Chercher une prestation existante pour ce jour
        existing = WorkEntry.search([
            ('employee_id', '=', self.employee_id.id),
            ('date', '=', self.date),
        ], limit=1)

        version = self.employee_id.current_version_id

        if resolution_type in ('leave_paid', 'leave_unpaid', 'sick', 'deduct'):
            # Cas absence: créer/modifier pour le temps manquant
            hours = abs(self.difference_hours) if self.difference_hours else self.theoretical_hours
            if existing:
                # Modifier le type et les heures
                existing.write({
                    'work_entry_type_id': work_entry_type.id,
                    'duration': hours,
                })
                return existing
            else:
                return WorkEntry.create({
                    'employee_id': self.employee_id.id,
                    'version_id': version.id if version else False,
                    'date': self.date,
                    'duration': hours,
                    'work_entry_type_id': work_entry_type.id,
                    'state': 'draft',
                })

        elif resolution_type == 'overtime_paid':
            # Cas HS: créer une prestation supplémentaire
            hours = self.difference_hours if self.difference_hours > 0 else 0
            if hours > 0:
                return WorkEntry.create({
                    'employee_id': self.employee_id.id,
                    'version_id': version.id if version else False,
                    'date': self.date,
                    'duration': hours,
                    'work_entry_type_id': work_entry_type.id,
                    'state': 'draft',
                })

        elif resolution_type == 'tolerate':
            # Cas toléré: s'assurer que la prestation normale existe
            if not existing:
                work100 = WorkEntryType.search([('code', '=', 'WORK100')], limit=1)
                return WorkEntry.create({
                    'employee_id': self.employee_id.id,
                    'version_id': version.id if version else False,
                    'date': self.date,
                    'duration': self.theoretical_hours,
                    'work_entry_type_id': work100.id if work100 else False,
                    'state': 'draft',
                })

        return False

    def action_open_work_entry(self):
        """Ouvrir la prestation liée"""
        self.ensure_one()
        if self.work_entry_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'hr.work.entry',
                'res_id': self.work_entry_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
