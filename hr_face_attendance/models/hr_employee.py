import base64
import logging

import numpy as np

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    face_embedding = fields.Binary(
        string='Embedding facial',
        attachment=False,
        copy=False,
        help='Vecteur ArcFace 512d encodé en base64 (float32). Généré via le wizard d\'enrollment.',
    )
    face_enrolled_date = fields.Datetime(
        string='Date d\'enrollment',
        readonly=True,
        copy=False,
    )
    face_enrolled_by = fields.Many2one(
        'res.users',
        string='Enregistré par',
        readonly=True,
        copy=False,
    )
    face_enrolled = fields.Boolean(
        string='Visage enregistré',
        compute='_compute_face_enrolled',
        store=True,
    )

    @api.depends('face_embedding')
    def _compute_face_enrolled(self):
        for emp in self:
            emp.face_enrolled = bool(emp.face_embedding)

    def _encode_embedding(self, arr):
        """numpy float32 512d → bytes base64 stockables en Binary."""
        return base64.b64encode(arr.astype(np.float32).tobytes())

    def _decode_embedding(self):
        """Bytes base64 stockés → numpy float32 array."""
        self.ensure_one()
        if not self.face_embedding:
            return None
        raw = base64.b64decode(self.face_embedding)
        return np.frombuffer(raw, dtype=np.float32)

    def action_open_face_enrollment(self):
        """Ouvre le wizard d'enrollment facial."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enregistrer le visage',
            'res_model': 'face.enrollment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_employee_id': self.id},
        }

    # ── Autorisation pointage facial ─────────────────────────────────────────

    face_portal_enabled = fields.Boolean(
        string='Pointage facial autorisé',
        default=False,
        tracking=True,
        help='Si activé, l\'employé peut pointer via reconnaissance faciale depuis le portail.',
    )

    # ── Géofence par employé ─────────────────────────────────────────────────

    geofence_enabled = fields.Boolean(
        string='Géofence actif',
        default=False,
        tracking=True,
        help='Si activé, le pointage facial depuis le portail est limité au périmètre défini.',
    )
    geofence_lat = fields.Float(
        string='Latitude centre',
        digits=(10, 7),
        tracking=True,
    )
    geofence_lng = fields.Float(
        string='Longitude centre',
        digits=(10, 7),
        tracking=True,
    )
    geofence_radius = fields.Integer(
        string='Rayon (mètres)',
        default=500,
        tracking=True,
    )

    # ── Mode itinéraire / checkpoints ───────────────────────────────────────

    checkpoint_mode = fields.Boolean(
        string='Pointage par itinéraire',
        default=False,
        tracking=True,
        help='Remplace le géofence unique par une liste de checkpoints à valider.',
    )
    checkpoint_period = fields.Selection(
        [
            ('daily', 'Journalière'),
            ('weekly', 'Hebdomadaire'),
            ('monthly', 'Mensuelle'),
        ],
        string='Période de ronde',
        default='daily',
        tracking=True,
        help='Fréquence à laquelle les checkpoints doivent être visités.',
    )
    checkpoint_ids = fields.One2many(
        'hr.employee.checkpoint',
        'employee_id',
        string='Checkpoints',
    )
    checkpoint_total = fields.Integer(
        string='Checkpoints programmés',
        compute='_compute_checkpoint_stats',
    )
    checkpoint_done_period = fields.Integer(
        string='Atteints (période)',
        compute='_compute_checkpoint_stats',
        help='Nombre de checkpoints ayant atteint leur objectif de visites sur la période courante.',
    )

    @api.depends('checkpoint_ids', 'checkpoint_ids.active', 'checkpoint_period')
    def _compute_checkpoint_stats(self):
        Log = self.env['hr.checkpoint.log']
        for emp in self:
            active_cps = emp.checkpoint_ids.filtered('active')
            emp.checkpoint_total = len(active_cps)
            if not active_cps:
                emp.checkpoint_done_period = 0
                continue
            date_from, date_to = emp._get_checkpoint_period_dates()
            done_count = 0
            for cp in active_cps:
                visits = Log.search_count([
                    ('checkpoint_id', '=', cp.id),
                    ('check_date', '>=', date_from),
                    ('check_date', '<=', date_to),
                ])
                if visits >= (cp.visits_required or 1):
                    done_count += 1
            emp.checkpoint_done_period = done_count

    def _get_checkpoint_period_dates(self):
        """Retourne (date_from, date_to) de la période courante."""
        from datetime import date, timedelta
        today = fields.Date.today()
        period = self.checkpoint_period or 'daily'
        if period == 'daily':
            return today, today
        elif period == 'weekly':
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        else:  # monthly
            start = today.replace(day=1)
            next_month = (start + timedelta(days=32)).replace(day=1)
            return start, next_month - timedelta(days=1)

    def _get_checkpoint_period_label(self):
        """Retourne un libellé lisible pour la période courante."""
        date_from, date_to = self._get_checkpoint_period_dates()
        period = self.checkpoint_period or 'daily'
        if period == 'daily':
            return date_from.strftime('%d/%m/%Y')
        elif period == 'weekly':
            return f"Semaine du {date_from.strftime('%d/%m')} au {date_to.strftime('%d/%m/%Y')}"
        else:
            return date_from.strftime('%B %Y')

    def action_detect_geofence_location(self):
        """Lance la détection GPS navigateur pour pré-remplir lat/lng."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'face_attendance.detect_location',
            'params': {'employee_id': self.id},
        }

    def action_clear_face_enrollment(self):
        """Supprime l'embedding et remet à zéro les métadonnées d'enrollment."""
        self.ensure_one()
        self.write({
            'face_embedding': False,
            'face_enrolled_date': False,
            'face_enrolled_by': False,
        })
