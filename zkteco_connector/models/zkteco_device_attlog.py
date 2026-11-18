# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta

import psycopg2
import pytz

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Fenêtre d'anti-rebond : plusieurs pointages d'un même employé dans cet
# intervalle sont considérés comme UN seul geste (cas "il pointe 4-5 fois à 8:00").
DEBOUNCE_SECONDS = 60

# Codes Status ZKTeco (doc T&A) → sens du pointage quand on respecte la touche.
STATUS_IN  = {0, 3, 4}   # Check In / Break In / Overtime In
STATUS_OUT = {1, 2, 5}   # Check Out / Break Out / Overtime Out


class ZktecoDeviceAttlog(models.Model):
    _name = 'zkteco.device.attlog'
    _description = 'ZKTeco Pointage brut'
    _order = 'timestamp desc'

    # Un geste physique = un pointage par (device, PIN, seconde). Le dispatch NATS
    # concurrent (run_in_executor) pouvait double-insérer le même punch (le dédup
    # par SELECT n'est pas atomique) ; la contrainte le garantit au niveau DB.
    _attlog_uniq_punch = models.Constraint(
        'unique(device_id, pin, timestamp)',
        'Un pointage identique (pointeuse, PIN, horodatage) existe déjà.')
    # Pointage du portail (sans pointeuse) : un par employé et par seconde.
    _attlog_uniq_portal = models.UniqueIndex(
        "(employee_id, timestamp) WHERE source = 'portal'",
        'Ce pointage du portail existe déjà.')

    # Pointeuse d'origine ; vide pour un pointage du portail (visage), qui
    # passe par le même sas et la même résolution que les pointeuses.
    device_id   = fields.Many2one('zkteco.device', ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', string='Société', compute='_compute_company_id',
                                 store=True, index=True)
    employee_id = fields.Many2one('hr.employee', index=True, ondelete='set null',
                                  help="Employé résolu via le PIN (vide tant que le PIN n'est pas mappé).")
    pin         = fields.Char(required=True, index=True)
    timestamp   = fields.Datetime(required=True)
    status      = fields.Integer(help='0=In 1=Out 2=Break Out 3=Break In 4=OT In 5=OT Out 255=sans touche')
    # Touche envoyée par la pointeuse, JAMAIS modifiée = piste d'audit légale.
    original_status = fields.Integer(string="Touche d'origine (code)", readonly=True)
    status_label = fields.Char(string='Touche (lib.)', compute='_compute_status_label')
    original_status_label = fields.Char(string="Touche d'origine",
                                        compute='_compute_status_label')
    # Sélecteur éditable par le RH (même jeu de touches que la pointeuse).
    touche = fields.Selection(
        selection='_selection_touche', string='Touche',
        compute='_compute_touche', inverse='_inverse_touche',
        help="Sens du pointage. Corrigible par le RH (ex. « Sortie » saisie au "
             "lieu d'« Entrée »). La touche d'origine reste tracée pour l'audit.")
    verify_mode = fields.Char()

    source = fields.Selection(
        [('device', 'Pointeuse'), ('manual', 'Manuel'), ('portal', 'Portail (visage)')],
        default='device', readonly=True, index=True, string='Source')
    is_corrected = fields.Boolean(string='Corrigé', compute='_compute_is_corrected',
                                  store=True)
    corrected_by = fields.Many2one('res.users', string='Corrigé par', readonly=True)
    correction_reason = fields.Char(string='Raison de la correction')

    state = fields.Selection([
        ('pending',   'En attente'),    # PIN non mappé, en quarantaine
        ('imported',  'Importé'),       # intégré dans un hr.attendance
        ('ignored',   'Ignoré'),        # écarté (anti-rebond / décision admin)
        ('duplicate', 'Doublon'),       # déjà reçu
        ('anomaly',   'Rejeté'),        # pointage non appariable (oubli, orphelin)
    ], default='pending', index=True)

    attendance_id = fields.Many2one('hr.attendance', string='Pointage créé', readonly=True)

    # Pointage du portail : position et scores, reportés sur la présence
    # (entrée et sortie séparément : la sortie n'écrase plus l'entrée).
    latitude = fields.Float(digits=(10, 7), readonly=True)
    longitude = fields.Float(digits=(10, 7), readonly=True)
    face_confidence = fields.Float(string='Confiance faciale', digits=(5, 4), readonly=True)
    liveness_score = fields.Float(string='Score de vivacité', digits=(5, 4), readonly=True)

    _STATUS_LABELS = {
        0: 'Entrée', 1: 'Sortie', 2: 'Pause (sortie)', 3: 'Pause (retour)',
        4: 'Heures sup (entrée)', 5: 'Heures sup (sortie)', 255: 'Sans touche',
    }

    @api.depends('device_id.company_id', 'employee_id.company_id')
    def _compute_company_id(self):
        for log in self:
            log.company_id = log.device_id.company_id or log.employee_id.company_id

    @api.model
    def _selection_touche(self):
        return [(str(k), v) for k, v in self._STATUS_LABELS.items()]

    @api.depends('status', 'original_status')
    def _compute_status_label(self):
        for log in self:
            log.status_label = self._STATUS_LABELS.get(log.status, f'Code {log.status}')
            log.original_status_label = self._STATUS_LABELS.get(
                log.original_status, f'Code {log.original_status}')

    @api.depends('status')
    def _compute_touche(self):
        valid = set(self._STATUS_LABELS)
        for log in self:
            log.touche = str(log.status) if log.status in valid else False

    def _inverse_touche(self):
        """Le RH change la touche → on garde l'originale, on trace le correcteur,
        et on re-résout la journée (le pointage faux/orphelin se réapparie)."""
        Employee = self.env['hr.employee'].sudo()
        for log in self:
            if not log.touche:
                continue
            new_status = int(log.touche)
            if new_status == log.status:
                continue
            log.status = new_status
            log.corrected_by = self.env.user.id
            if log.employee_id and log.timestamp:
                tz = self._employee_tz(log.employee_id)
                day = pytz.utc.localize(log.timestamp).astimezone(tz).date()
                self._resolve_one_day(Employee.browse(log.employee_id.id), day)

    @api.depends('status', 'original_status')
    def _compute_is_corrected(self):
        for log in self:
            log.is_corrected = log.status != log.original_status

    def init(self):
        """Backfill : la touche d'origine des pointages déjà reçus = leur touche
        actuelle (avant toute correction RH)."""
        self.env.cr.execute(
            "UPDATE zkteco_device_attlog SET original_status = status "
            "WHERE original_status IS NULL")

    # ══════════════════════════════════════════════════════════════════
    #  AJOUT MANUEL — pointage oublié (badge oublié)
    # ══════════════════════════════════════════════════════════════════

    @api.model_create_multi
    def create(self, vals_list):
        """Un pointage saisi à la main (source='manual') est tracé et déclenche
        la re-résolution de la journée — exactement comme un punch device."""
        for vals in vals_list:
            if vals.get('source') != 'manual':
                continue
            vals.setdefault('corrected_by', self.env.uid)
            vals.setdefault('state', 'pending')
            if not vals.get('pin') and vals.get('employee_id'):
                emp = self.env['hr.employee'].browse(vals['employee_id'])
                vals['pin'] = emp.zkteco_pin or '0'
        records = super().create(vals_list)
        manual = records.filtered(lambda r: r.source == 'manual')
        for r in manual:
            # punch manuel = pas une correction de touche → origine == touche.
            if r.original_status != r.status:
                r.original_status = r.status
        to_resolve = manual.filtered('employee_id')
        if to_resolve:
            to_resolve._resolve_days_for_logs()
        return records

    def action_add_missing_punch(self):
        """Ouvre un formulaire de saisie d'un pointage manquant, pré-rempli depuis
        l'anomalie courante (même employé, même pointeuse, même jour)."""
        self.ensure_one()
        ctx = {
            'default_employee_id':      self.employee_id.id,
            'default_device_id':        self.device_id.id,
            'default_pin':              self.pin,
            'default_source':           'manual',
            'default_status':           0,  # Entrée par défaut
            'default_correction_reason': 'Pointage oublié',
        }
        if self.timestamp:
            ctx['default_timestamp'] = fields.Datetime.to_string(
                self._suggest_entry_dt(self.employee_id, self.timestamp))
        return {
            'type':      'ir.actions.act_window',
            'name':      'Ajouter le pointage manquant',
            'res_model': 'zkteco.device.attlog',
            'view_mode': 'form',
            'views': [(self.env.ref(
                'zkteco_connector.view_zkteco_attlog_manual_form').id, 'form')],
            'target':  'new',
            'context': ctx,
        }

    @api.model
    def _suggest_entry_dt(self, employee, ref_ts):
        """Heure d'entrée suggérée = embauche prévue le jour du punch orphelin,
        à défaut 08:00 local."""
        tz = self._employee_tz(employee) if employee else pytz.utc
        day = pytz.utc.localize(ref_ts).astimezone(tz).date()
        hour = 8.0
        cal = employee.resource_calendar_id if employee else None
        if cal:
            dow = str(day.weekday())
            lines = cal.attendance_ids.filtered(lambda l: l.dayofweek == dow)
            if lines:
                hour = min(lines.mapped('hour_from'))
        h, m = int(hour), int((hour % 1) * 60)
        local = tz.localize(datetime(day.year, day.month, day.day, h, m))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    # ══════════════════════════════════════════════════════════════════
    #  INGESTION — appelée depuis le handler NATS
    # ══════════════════════════════════════════════════════════════════

    @api.model
    def _store_or_process(self, serial_number: str, pin: str, timestamp,
                          status: int, verify_mode: str):
        """Stocke TOUJOURS le pointage brut, puis :
          - PIN mappé   → résout la journée concernée (brut → hr.attendance)
          - PIN inconnu → reste en quarantaine jusqu'au mapping
        """
        device = self.env['zkteco.device'].sudo().search(
            [('serial_number', '=', serial_number)], limit=1)
        if not device:
            return

        employee = self.env['hr.employee'].sudo().search(
            [('zkteco_pin', '=', pin)], limit=1)

        log = self._stage(device, pin, timestamp, status, verify_mode,
                          employee=employee)

        if not employee:
            # PIN non mappé : on garantit la présence dans le sas pour le mapping
            self.env['zkteco.device.user'].sudo()._upsert(serial_number, pin, '', 0, '')
            return

        if log:
            log._resolve_days_for_logs()

    def _stage(self, device, pin, timestamp, status, verify_mode, employee=None):
        """Crée le pointage brut (avec dédup). Retourne le record (ou existant)."""
        # Dédup contre la touche D'ORIGINE : si le RH a re-tagué un punch et que
        # la pointeuse re-pousse le même geste, on le reconnaît quand même.
        existing = self.sudo().search([
            ('device_id', '=', device.id),
            ('pin',       '=', pin),
            ('timestamp', '=', timestamp),
            '|', ('status', '=', status), ('original_status', '=', status),
        ], limit=1)
        if existing:
            if employee and not existing.employee_id:
                existing.employee_id = employee.id
            return existing
        try:
            with self.env.cr.savepoint():
                return self.sudo().create({
                    'device_id':       device.id,
                    'employee_id':     employee.id if employee else False,
                    'pin':             pin,
                    'timestamp':       timestamp,
                    'status':          status,
                    'original_status': status,
                    'source':          'device',
                    'verify_mode':     verify_mode or '',
                    'state':           'pending',
                })
        except psycopg2.IntegrityError:
            # Course avec un autre dispatch concurrent sur le même punch : la
            # contrainte unique a tranché. On récupère la ligne gagnante.
            existing = self.sudo().search([
                ('device_id', '=', device.id),
                ('pin',       '=', pin),
                ('timestamp', '=', timestamp),
            ], limit=1)
            if existing and employee and not existing.employee_id:
                existing.employee_id = employee.id
            return existing

    @api.model
    def _stage_portal(self, employee, timestamp, latitude=0.0, longitude=0.0,
                      face_confidence=0.0, liveness_score=0.0):
        """Pointage du portail (visage) : il entre dans le MÊME sas que ceux des
        pointeuses puis la journée est résolue par la même règle (anti-rafale,
        appariement, anomalies, respect des corrections RH). Sens déduit par
        alternance (pas de touche). Retourne le pointage brut."""
        log = self.sudo().create({
            'employee_id': employee.id,
            'pin': employee.zkteco_pin or 'portail',
            'timestamp': timestamp,
            'status': 255,
            'original_status': 255,
            'source': 'portal',
            'verify_mode': 'face',
            'state': 'pending',
            'latitude': latitude or 0.0,
            'longitude': longitude or 0.0,
            'face_confidence': face_confidence or 0.0,
            'liveness_score': liveness_score or 0.0,
        })
        log._resolve_days_for_logs()
        return log

    def _import_to_attendance(self, employee):
        """Appelé au mapping d'un PIN : rattache les bruts en quarantaine à
        l'employé puis résout les journées concernées."""
        pending = self.filtered(lambda l: not l.employee_id or l.employee_id == employee)
        pending.write({'employee_id': employee.id})
        pending._resolve_days_for_logs()

    # ══════════════════════════════════════════════════════════════════
    #  RÉSOLUTION — brut → hr.attendance
    # ══════════════════════════════════════════════════════════════════

    def _resolve_days_for_logs(self):
        """Regroupe self par (employé, jour local) et résout chaque journée."""
        to_resolve = set()
        for log in self:
            if not log.employee_id:
                continue
            to_resolve.add((log.employee_id.id, self._work_day_of(log.employee_id, log.timestamp)))
        Employee = self.env['hr.employee'].sudo()
        for emp_id, day in to_resolve:
            self._resolve_one_day(Employee.browse(emp_id), day)

    @api.model
    def _day_offset_hours(self, employee):
        """Début de la « journée de travail » en heures après minuit.

        0 pour un horaire de jour. Pour un poste de nuit (un créneau qui finit
        après minuit, ex. 22 h-6 h), la journée commence 6 h avant la prise de
        poste (22 h → 16 h) : entrée et sortie tombent dans la MÊME journée et
        ne sont plus coupées en deux à minuit."""
        cal = employee.resource_calendar_id
        if not cal:
            return 0.0
        night_starts = [line.hour_from for line in cal.attendance_ids
                        if line.day_period != 'lunch'
                        and (line.hour_to > 24 or line.hour_to <= line.hour_from)]
        if not night_starts:
            return 0.0
        return max(0.0, min(night_starts) - 6.0)

    @api.model
    def _work_day_of(self, employee, timestamp):
        """Journée de travail d'un horodatage UTC (voir _day_offset_hours)."""
        tz = self._employee_tz(employee)
        local = pytz.utc.localize(timestamp).astimezone(tz).replace(tzinfo=None)
        return (local - timedelta(hours=self._day_offset_hours(employee))).date()

    @api.model
    def _lock_day(self, employee_id, day):
        """Verrou transactionnel par (employé, journée) : la résolution des
        pointages (consommateur NATS, cron, portail) et le recalcul des
        prestations ne travaillent jamais à deux sur le même jour."""
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (int(employee_id), day.toordinal()))

    @api.model
    def _resolve_one_day(self, employee, day):
        """Reconstruit les hr.attendance d'un employé pour une journée de travail."""
        self._lock_day(employee.id, day)
        tz = self._employee_tz(employee)
        start_local = tz.localize(datetime.combine(day, datetime.min.time())
                                  + timedelta(hours=self._day_offset_hours(employee)))
        start_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)
        end_utc = (start_local + timedelta(days=1)).astimezone(pytz.utc).replace(tzinfo=None)

        raws = self.sudo().search([
            ('employee_id', '=', employee.id),
            ('timestamp', '>=', start_utc),
            ('timestamp', '<',  end_utc),
        ], order='timestamp asc')

        kept = self._debounce(raws)
        use_punch = self._use_punch_state(employee)
        dirs = self._directions(kept, use_punch)
        segments, anomalies, trailing = self._pair(dirs)

        has_later = self.sudo().search_count([
            ('employee_id', '=', employee.id),
            ('timestamp', '>=', end_utc),
        ]) > 0

        self._rebuild_attendance(employee, start_utc, end_utc,
                                 segments, anomalies, trailing, has_later)

        # Pauses : seulement en mode punch ON (les touches Pause sortie/retour
        # bornent le trou). En mode déduction OFF, un trou n'est pas typé.
        if use_punch:
            self._rebuild_breaks(employee, day, start_utc, end_utc,
                                 segments, trailing)

    def _debounce(self, raws):
        """Réduit les rafales : on garde le 1er pointage, puis on ignore tout
        ce qui suit dans DEBOUNCE_SECONDS (les suivants passent en 'ignored')."""
        kept = []
        last_ts = None
        for log in raws:
            if last_ts is None or (log.timestamp - last_ts).total_seconds() >= DEBOUNCE_SECONDS:
                kept.append(log)
                last_ts = log.timestamp
            else:
                if log.state != 'ignored':
                    log.state = 'ignored'
                    log.attendance_id = False
        return kept

    def _directions(self, logs, use_punch):
        """Affecte 'in'/'out' à chaque pointage.
          - use_punch=True  : on respecte la touche ; 255/inconnu → alternance
          - use_punch=False : déduction pure par alternance (1er=in, puis bascule)
        """
        result = []
        expected = 'in'
        for log in logs:
            direction = None
            if use_punch:
                if log.status in STATUS_IN:
                    direction = 'in'
                elif log.status in STATUS_OUT:
                    direction = 'out'
            if direction is None:
                direction = expected
            result.append((direction, log))
            expected = 'out' if direction == 'in' else 'in'
        return result

    @staticmethod
    def _pair(dirs):
        """Apparie in→out en segments. Retourne (segments, anomalies, trailing).
          - segments : liste de (in_log, out_log)
          - anomalies: pointages non appariables (double entrée, sortie orpheline)
          - trailing : dernière entrée sans sortie (oubli potentiel) ou None
        """
        segments, anomalies = [], []
        open_log = None
        for direction, log in dirs:
            if direction == 'in':
                if open_log is None:
                    open_log = log
                else:
                    anomalies.append(open_log)   # 2 entrées de suite
                    open_log = log
            else:  # out
                if open_log is not None:
                    segments.append((open_log, log))
                    open_log = None
                else:
                    anomalies.append(log)        # sortie orpheline
        return segments, anomalies, open_log

    def _rebuild_attendance(self, employee, start_utc, end_utc,
                            segments, anomalies, trailing, has_later):
        """Idempotent par DIFF : on rapproche les segments calculés des
        hr.attendance existants via leur `zkteco_transaction_id` (déterministe).
          - segment identique déjà présent → on n'y touche pas (l'ID reste stable)
          - check_out qui a bougé          → simple write
          - segment nouveau                → create
          - présence gérée sans segment    → unlink (obsolète)
        Les présences corrigées à la main (zkteco_manual_edit) ne sont jamais
        touchées. Respecte la contrainte « 1 seule présence ouverte » d'Odoo."""
        # zkteco_skip_manual_flag : les écritures du moteur ne doivent PAS être
        # prises pour des corrections manuelles par le write() de hr.attendance.
        Att = self.env['hr.attendance'].sudo().with_context(zkteco_skip_manual_flag=True)

        zkteco_day = Att.search([
            ('employee_id', '=', employee.id),
            ('zkteco_transaction_id', '!=', False),
            ('check_in', '>=', start_utc),
            ('check_in', '<',  end_utc),
        ])
        manual  = zkteco_day.filtered('zkteco_manual_edit')
        managed = zkteco_day - manual
        by_txn  = {a.zkteco_transaction_id: a for a in managed}
        keep_ids = set()

        def _clash(c_in, c_out):
            """Présence manuelle chevauchant l'intervalle (c_out=None → ouvert)."""
            for m in manual:
                m_out = m.check_out or end_utc
                s_out = c_out or end_utc
                if c_in < m_out and m.check_in < s_out:
                    return m
            return False

        def _upsert(in_log, out_log):
            """Crée ou met à jour la présence du segment, sans churn d'ID."""
            vals = self._att_vals(in_log, out_log)
            att = by_txn.get(vals['zkteco_transaction_id'])
            if att:
                # Réconcilie tous les champs (auto-réparation des anciens records
                # quand on ajoute une colonne), sans toucher check_in (clé du txn).
                scalar = ('check_out', 'in_mode', 'out_mode',
                          'zkteco_verify_mode', 'zkteco_out_verify_mode',
                          'zkteco_in_operation', 'zkteco_out_operation',
                          'zkteco_device_sn', 'in_latitude', 'in_longitude',
                          'out_latitude', 'out_longitude')
                changed = {k: vals.get(k, False)
                           for k in scalar if att[k] != vals.get(k, False)}
                for k in ('zkteco_device_id', 'zkteco_out_device_id'):
                    if att[k].id != vals.get(k, False):
                        changed[k] = vals.get(k, False)
                if changed:
                    att.write(changed)
            else:
                att = Att.create(vals)
            keep_ids.add(att.id)
            return att

        for in_log, out_log in segments:
            clash = _clash(in_log.timestamp, out_log.timestamp)
            if clash:
                # déjà couvert par une correction RH : on rattache sans recréer
                (in_log + out_log).write({'state': 'imported', 'attendance_id': clash.id})
                continue
            att = _upsert(in_log, out_log)
            (in_log + out_log).write({'state': 'imported', 'attendance_id': att.id})

        for log in anomalies:
            log.write({'state': 'anomaly', 'attendance_id': False})

        if trailing:
            clash = _clash(trailing.timestamp, None)
            if clash:
                trailing.write({'state': 'imported', 'attendance_id': clash.id})
            elif has_later:
                # un oubli de sortie passé : pas de présence inventée, on flague
                trailing.write({'state': 'anomaly', 'attendance_id': False})
            else:
                # dernière entrée, rien après : présence ouverte (l'employé est
                # peut-être encore là). On garantit l'unicité de la présence ouverte.
                self._close_dangling_open(employee, keep_ids=keep_ids,
                                          by_txn=by_txn, trailing=trailing)
                att = _upsert(trailing, None)
                trailing.write({'state': 'imported', 'attendance_id': att.id})

        # Présences gérées par le moteur qui n'ont plus de segment → obsolètes.
        # .exists() : _close_dangling_open() a pu déjà supprimer certaines de ces
        # présences ouvertes (leur id reste dans `managed`, périmé) → sans ce filtre
        # on relance unlink() sur un fantôme = MissingError.
        obsolete = managed.filtered(lambda a: a.id not in keep_ids).exists()
        if obsolete:
            self.sudo().search([('attendance_id', 'in', obsolete.ids)]).write({'attendance_id': False})
            obsolete.unlink()

    def _close_dangling_open(self, employee, keep_ids=(), by_txn=None, trailing=None):
        """Supprime toute présence ZKTeco ouverte résiduelle (Odoo n'autorise
        qu'une seule présence ouverte par employé), SAUF celle qui correspond au
        segment courant (pour éviter le churn). Les bruts redeviennent anomalies."""
        Att = self.env['hr.attendance'].sudo().with_context(zkteco_skip_manual_flag=True)
        keep = set(keep_ids)
        if by_txn is not None and trailing is not None:
            keep_att = by_txn.get(self._att_vals(trailing)['zkteco_transaction_id'])
            if keep_att:
                keep.add(keep_att.id)
        opens = Att.search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False),
            ('zkteco_transaction_id', '!=', False),
            ('zkteco_manual_edit', '=', False),
            ('id', 'not in', list(keep)),
        ])
        if opens:
            self.sudo().search([('attendance_id', 'in', opens.ids)]).write(
                {'attendance_id': False, 'state': 'anomaly'})
            opens.unlink()

    # ── pauses réelles (Vision B — contrôle RH) ──────────────────────

    def _rebuild_breaks(self, employee, day, start_utc, end_utc, segments, trailing):
        """Idempotent : reconstruit les pauses réelles du jour à partir des
        segments. Une pause = trou ouvert par « Pause sortie » (status 2) ;
        sa fin = le pointage qui rouvre un segment ensuite (vide = en cours)."""
        Break = self.env['zkteco.attendance.break'].sudo()
        # On ne reconstruit QUE les pauses pointées (source device). Les pauses
        # saisies manuellement par le RH sont protégées (jamais écrasées/supprimées).
        existing = Break.search([
            ('employee_id', '=', employee.id),
            ('break_start', '>=', start_utc),
            ('break_start', '<',  end_utc),
            ('source', '=', 'device'),
        ])
        by_start = {b.break_start: b for b in existing}
        allowed = self._allowed_break_hours(employee, day)
        cal = employee.resource_calendar_id
        tolerance = (cal.zkteco_break_tolerance or 0) / 60.0 if cal else 0.0
        keep = set()

        n = len(segments)
        for idx, (in_log, out_log) in enumerate(segments):
            if out_log.status != 2:        # uniquement les pauses explicites
                continue
            # pointage qui clôt la pause = entrée du segment suivant (ou trailing)
            if idx + 1 < n:
                next_in = segments[idx + 1][0]
            else:
                next_in = trailing or None

            vals = {
                'employee_id':      employee.id,
                'device_id':        out_log.device_id.id,
                'break_start':      out_log.timestamp,
                'break_end':        next_in.timestamp if next_in else False,
                'duration_allowed': allowed,
                'tolerance':        tolerance,
            }
            b = by_start.get(out_log.timestamp)
            if b:
                b.write(vals)
            else:
                b = Break.create(vals)
            keep.add(b.id)

        obsolete = existing.filtered(lambda x: x.id not in keep)
        if obsolete:
            obsolete.unlink()

    @staticmethod
    def _allowed_break_hours(employee, day):
        """Durée de pause prévue par l'horaire de l'employé pour ce jour (heures)."""
        cal = employee.resource_calendar_id
        if not cal:
            return 0.0
        dow = str(day.weekday())
        lines = cal.attendance_ids.filtered(
            lambda l: l.day_period == 'lunch' and l.dayofweek == dow)
        return sum(l.hour_to - l.hour_from for l in lines)

    # ── helpers ───────────────────────────────────────────────────────

    def _mode_of(self, log):
        """Mode de pointage Odoo du pointage brut."""
        if log.source == 'portal':
            modes = dict(self.env['hr.attendance']._fields['in_mode'].selection)
            return 'face' if 'face' in modes else 'manual'
        return 'zkteco'

    def _att_vals(self, in_log, out_log=None):
        sn = in_log.device_id.serial_number or 'PORTAIL'
        vals = {
            'employee_id': in_log.employee_id.id,
            'check_in': in_log.timestamp,
            'zkteco_device_id': in_log.device_id.id or False,
            'zkteco_device_sn': in_log.device_id.serial_number or False,
            'zkteco_transaction_id': f"{sn}_{in_log.timestamp.isoformat()}_{in_log.pin}",
            'zkteco_verify_mode': in_log.verify_mode or False,
            'zkteco_in_operation': self._STATUS_LABELS.get(in_log.status, f'Code {in_log.status}'),
            'in_mode': self._mode_of(in_log),
        }
        if in_log.source == 'portal':
            vals.update(in_latitude=in_log.latitude, in_longitude=in_log.longitude)
        if out_log:
            vals['check_out'] = out_log.timestamp
            vals['out_mode'] = self._mode_of(out_log)
            vals['zkteco_out_device_id'] = out_log.device_id.id or False
            vals['zkteco_out_verify_mode'] = out_log.verify_mode or False
            vals['zkteco_out_operation'] = self._STATUS_LABELS.get(
                out_log.status, f'Code {out_log.status}')
            if out_log.source == 'portal':
                vals.update(out_latitude=out_log.latitude, out_longitude=out_log.longitude)
        return vals

    @staticmethod
    def _employee_tz(employee):
        tzname = (employee.tz
                  or (employee.resource_calendar_id and employee.resource_calendar_id.tz)
                  or 'UTC')
        try:
            return pytz.timezone(tzname)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _use_punch_state(self, employee):
        cal = employee.resource_calendar_id
        return cal.zkteco_use_punch_state if cal else True

    # ── cron : filet de sécurité (rejeu des journées récentes) ────────

    @api.model
    def _cron_resolve_recent(self, days_back=3):
        """Re-résout les N derniers jours pour les employés ayant des bruts
        récents : rattrape les pointages tardifs, les changements de toggle,
        et nettoie les présences ouvertes périmées."""
        since = datetime.utcnow() - timedelta(days=days_back)
        recent = self.sudo().search([
            ('employee_id', '!=', False),
            ('timestamp', '>=', since),
        ])
        recent._resolve_days_for_logs()