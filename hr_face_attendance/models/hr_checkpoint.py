from datetime import timedelta

from odoo import models, fields, api


class HrEmployeeCheckpoint(models.Model):
    """Point de passage obligatoire défini par le RH sur la fiche employé."""
    _name = 'hr.employee.checkpoint'
    _description = 'Checkpoint itinéraire employé'
    _order = 'sequence, id'

    employee_id = fields.Many2one(
        'hr.employee',
        string='Employé',
        required=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(string='Nom du checkpoint', required=True)
    sequence = fields.Integer(default=10)
    geofence_lat = fields.Float(string='Latitude', digits=(10, 7), required=True)
    geofence_lng = fields.Float(string='Longitude', digits=(10, 7), required=True)
    geofence_radius = fields.Integer(string='Rayon (m)', default=200)
    visits_required = fields.Integer(
        string='Visites requises',
        default=1,
        help='Nombre de visites à effectuer par période (jour/semaine/mois).',
    )
    visits_done_period = fields.Integer(
        string='Visites effectuées',
        compute='_compute_visits_done_period',
    )
    active = fields.Boolean(default=True)

    @api.depends('employee_id.checkpoint_period')
    def _compute_visits_done_period(self):
        Log = self.env['hr.checkpoint.log']
        for cp in self:
            date_from, date_to = cp.employee_id._get_checkpoint_period_dates()
            cp.visits_done_period = Log.search_count([
                ('checkpoint_id', '=', cp.id),
                ('check_date', '>=', date_from),
                ('check_date', '<=', date_to),
            ])

    def action_detect_location(self):
        """Lance la détection GPS navigateur pour pré-remplir lat/lng du checkpoint."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'face_attendance.detect_location',
            'params': {'checkpoint_id': self.id},
        }


class HrCheckpointLog(models.Model):
    """Trace chaque validation de checkpoint par un employé."""
    _name = 'hr.checkpoint.log'
    _description = 'Log de checkpoint itinéraire'
    _order = 'check_datetime desc'

    employee_id = fields.Many2one(
        'hr.employee',
        string='Employé',
        required=True,
        ondelete='cascade',
        index=True,
    )
    checkpoint_id = fields.Many2one(
        'hr.employee.checkpoint',
        string='Checkpoint',
        required=True,
        ondelete='cascade',
    )
    check_date = fields.Date(
        string='Date',
        required=True,
        default=fields.Date.today,
        index=True,
    )
    check_datetime = fields.Datetime(string='Heure', required=True, default=fields.Datetime.now)
    latitude = fields.Float(string='Latitude GPS', digits=(10, 7))
    longitude = fields.Float(string='Longitude GPS', digits=(10, 7))
    face_confidence = fields.Float(string='Confiance faciale', digits=(5, 4))

    def action_open_day_map(self):
        """Ouvre la carte itinéraire de la période (validés + manquants)."""
        self.ensure_one()
        date_from, date_to = self.employee_id._get_checkpoint_period_dates()
        return {
            'type': 'ir.actions.client',
            'tag': 'face_attendance.checkpoint_day_map',
            'params': {
                'employee_id': self.employee_id.id,
                'employee_name': self.employee_id.name,
                'date_from': str(date_from),
                'date_to': str(date_to),
                'period_label': self.employee_id._get_checkpoint_period_label(),
            },
        }

    @api.model
    def get_period_map_data(self, employee_id, date_from, date_to):
        """Retourne checkpoints + stats de visites pour la carte OWL.

        Appelé via RPC depuis le client action.
        Returns liste de dicts avec done_count, required, status (full/partial/none).
        """
        employee = self.env['hr.employee'].browse(employee_id)
        checkpoints = employee.checkpoint_ids.filtered('active').sorted('sequence')
        logs = self.search([
            ('employee_id', '=', employee_id),
            ('check_date', '>=', date_from),
            ('check_date', '<=', date_to),
        ])

        # Grouper les logs par checkpoint
        visits_by_cp = {}
        for log in logs:
            cp_id = log.checkpoint_id.id
            if cp_id not in visits_by_cp:
                visits_by_cp[cp_id] = []
            visits_by_cp[cp_id].append(log)

        result = []
        for cp in checkpoints:
            cp_logs = visits_by_cp.get(cp.id, [])
            done_count = len(cp_logs)
            required = cp.visits_required or 1
            last_log = cp_logs[0] if cp_logs else None  # trié desc

            if done_count == 0:
                status = 'none'
            elif done_count >= required:
                status = 'full'
            else:
                status = 'partial'

            result.append({
                'id': cp.id,
                'name': cp.name,
                'lat': cp.geofence_lat,
                'lng': cp.geofence_lng,
                'radius': cp.geofence_radius,
                'visits_done': done_count,
                'visits_required': required,
                'status': status,
                'last_visit': last_log.check_datetime.strftime('%d/%m %H:%M') if last_log else None,
                'last_lat': last_log.latitude if last_log else None,
                'last_lng': last_log.longitude if last_log else None,
            })
        return result
