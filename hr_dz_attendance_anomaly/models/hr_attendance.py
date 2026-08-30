# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta

import pytz

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    # Entrée manquante : alimenté par le moteur de pointage (ex. zkteco) quand un
    # pointage de sortie orphelin est détecté. Générique, sans dépendance device.
    checkout_only = fields.Boolean(
        string='Entrée manquante',
        default=False,
        help="Coché quand seul un pointage de sortie a été détecté sans entrée correspondante.",
    )

    anomaly_ids = fields.Many2many(
        'hr.attendance.anomaly.type',
        'hr_attendance_anomaly_rel',
        'attendance_id',
        'anomaly_type_id',
        string='Anomalies',
    )
    is_anomaly = fields.Boolean(
        string='Anomalie',
        compute='_compute_anomalies',
        store=True,
        help='Filtre rapide pour les présences avec anomalies',
    )
    anomaly_count = fields.Integer(
        string='Nb anomalies',
        compute='_compute_anomalies',
        store=True,
    )

    # Comparaison avec le planning
    scheduled_start = fields.Datetime(string='Début prévu', help='Heure de début selon le planning')
    scheduled_end = fields.Datetime(string='Fin prévue', help='Heure de fin selon le planning')
    late_minutes = fields.Integer(string='Retard (min)', compute='_compute_anomalies', store=True)
    early_leave_minutes = fields.Integer(string='Départ anticipé (min)', compute='_compute_anomalies', store=True)

    # ── Lignes de déduction (retard / départ anticipé) ───────────────────────
    deduction_ids = fields.One2many(
        'hr.attendance.deduction', 'attendance_id', string='Déductions')

    deduction_status = fields.Selection([
        ('to_validate', 'À valider'),
        ('validated',   'Validée'),
        ('refused',     'Refusée'),
    ], string='État déductions',
       compute='_compute_deduction_status', store=True, index=True)

    # Droits manager/officer (pour les boutons valider/refuser en ligne)
    is_manager = fields.Boolean(compute='_compute_is_manager')

    @api.depends('employee_id')
    def _compute_is_manager(self):
        has_mgr = self.env.user.has_group('hr_attendance.group_hr_attendance_manager')
        has_off = self.env.user.has_group('hr_attendance.group_hr_attendance_officer')
        for att in self:
            att.is_manager = has_mgr or (
                has_off and att.employee_id.attendance_manager_id == self.env.user)

    @api.depends('deduction_ids.status')
    def _compute_deduction_status(self):
        for att in self:
            statuses = att.deduction_ids.mapped('status')
            if not statuses:
                att.deduction_status = False
            elif 'to_validate' in statuses:
                att.deduction_status = 'to_validate'
            elif all(s == 'validated' for s in statuses):
                att.deduction_status = 'validated'
            else:
                att.deduction_status = 'refused'

    def _sync_deduction_lines(self, late_min, early_min):
        """Crée / met à jour les lignes hr.attendance.deduction selon les minutes calculées.

        - Crée une ligne si aucune n'existe pour ce type.
        - Met à jour la durée si la ligne est encore 'to_validate'.
        - Supprime les lignes 'to_validate' si l'anomalie a disparu.
        - Ne touche jamais les lignes déjà 'validated' ou 'refused'.
        """
        self.ensure_one()
        if not self.check_in or not self.employee_id:
            return
        Deduction = self.env['hr.attendance.deduction']
        date = self.check_in.date()

        for dtype, minutes in [('late', late_min), ('early', early_min)]:
            existing = self.deduction_ids.filtered(lambda d: d.deduction_type == dtype)
            if minutes > 0:
                hours = minutes / 60.0
                pending = existing.filtered(lambda d: d.status == 'to_validate')
                if pending:
                    pending.write({'duration': hours})
                elif not existing:
                    Deduction.create({
                        'employee_id':   self.employee_id.id,
                        'attendance_id': self.id,
                        'date':          date,
                        'deduction_type': dtype,
                        'duration':      hours,
                    })
            else:
                existing.filtered(lambda d: d.status == 'to_validate').unlink()

    def _detect_anomalies(self):
        """Évalue toutes les règles actives via leur code Python.

        Retourne : (triggered_recordset, late_min, early_min)
        """
        self.ensure_one()
        triggered = self.env['hr.attendance.anomaly.type'].browse()
        late_min = 0
        early_min = 0

        if not self.check_in:
            return triggered, late_min, early_min

        worked_minutes = 0.0
        if self.check_out:
            worked_minutes = (self.check_out - self.check_in).total_seconds() / 60

        # Tolérances depuis la société (remplace l'ancienne biotime.config)
        company = self.employee_id.company_id or self.env.company
        late_tolerance = company.late_tolerance_minutes or 5
        early_tolerance = company.early_tolerance_minutes or 5

        rules = self.env['hr.attendance.anomaly.type'].search([
            ('active', '=', True),
            ('condition_code', '!=', False),
        ])

        for rule in rules:
            localdict = {
                'check_in':        self.check_in,
                'check_out':       self.check_out,
                'scheduled_start': self.scheduled_start,
                'scheduled_end':   self.scheduled_end,
                'worked_minutes':  worked_minutes,
                'worked_hours':    worked_minutes / 60,
                'employee':        self.employee_id,
                'checkout_only':   self.checkout_only,
                'late_tolerance':  late_tolerance,
                'early_tolerance': early_tolerance,
                'result':          False,
                'late_min':        0,
                'early_min':       0,
            }
            try:
                safe_eval(rule.condition_code, localdict, mode='exec')
            except Exception as e:
                _logger.warning("Anomalie '%s' — erreur code : %s", rule.name, e)
                continue

            if localdict.get('result'):
                triggered |= rule
                late_min = max(late_min, localdict.get('late_min', 0))
                early_min = max(early_min, localdict.get('early_min', 0))

        # ── Conscience des SEGMENTS (jour découpé par des pauses) ───────────
        # Le moteur a été conçu pour entrée/sortie simple. En mode punch-state,
        # une journée est scindée en plusieurs présences (segments) par les
        # pauses. Le retard ne vaut que pour le PREMIER segment, le départ
        # anticipé que pour le DERNIER ; les bornes intermédiaires (sortie/retour
        # de pause) ne sont ni un retard ni un départ ni une sortie manquante.
        segs = self._day_segments()
        if len(segs) > 1:
            is_first = segs[0].id == self.id
            is_last = segs[-1].id == self.id
            if not is_first:
                late_min = 0
                triggered -= triggered.filtered(lambda r: r.code == 'late')
            if not is_last:
                early_min = 0
                triggered -= triggered.filtered(
                    lambda r: r.code in ('early_leave', 'no_checkout'))

        # Sortie pas encore pointée sur une journée EN COURS (l'employé est
        # peut-être encore là) → on n'annonce pas une "sortie manquante".
        if not self.check_out:
            tz = self._anomaly_tz()
            seg_day = pytz.utc.localize(self.check_in).astimezone(tz).date()
            if seg_day >= datetime.now(tz).date():
                triggered -= triggered.filtered(lambda r: r.code == 'no_checkout')

        # Segment clôturé par une PAUSE EN COURS (sortie de pause non suivie d'un
        # retour) : l'employé est en pause, pas parti → ni départ anticipé ni
        # sortie manquante. (Un abus de pause est traité par le suivi des pauses,
        # pas par une fausse anomalie de présence.)
        if self._has_open_break():
            early_min = 0
            triggered -= triggered.filtered(
                lambda r: r.code in ('early_leave', 'no_checkout'))

        return triggered, late_min, early_min

    # ── Segments de journée ──────────────────────────────────────────────────

    @api.model
    def _tz_for_employee(self, employee):
        name = (employee.tz
                or (employee.resource_calendar_id and employee.resource_calendar_id.tz)
                or 'Africa/Algiers')
        try:
            return pytz.timezone(name)
        except Exception:
            return pytz.timezone('Africa/Algiers')

    def _anomaly_tz(self):
        self.ensure_one()
        return self._tz_for_employee(self.employee_id)

    def _day_segments_for(self, emp_id, day):
        """Tous les segments (présences) d'un employé pour une journée locale,
        triés par heure d'arrivée."""
        emp = self.env['hr.employee'].browse(emp_id)
        tz = self._tz_for_employee(emp)
        start = tz.localize(datetime(day.year, day.month, day.day)).astimezone(
            pytz.UTC).replace(tzinfo=None)
        end = start + timedelta(days=1)
        return self.search([
            ('employee_id', '=', emp_id),
            ('check_in', '>=', start),
            ('check_in', '<', end),
        ], order='check_in')

    def _has_open_break(self):
        """Vrai si ce segment se termine sur une pause EN COURS (sortie de pause
        sans retour pointé) — l'employé est en pause, pas parti.
        Couplage souple : sans le module de pauses (zkteco), retourne False."""
        self.ensure_one()
        if not self.check_out or 'zkteco.attendance.break' not in self.env:
            return False
        return bool(self.env['zkteco.attendance.break'].sudo().search_count([
            ('employee_id', '=', self.employee_id.id),
            ('break_end', '=', False),
            ('break_start', '=', self.check_out),
        ]))

    def _day_segments(self):
        self.ensure_one()
        if not self.check_in or not self.employee_id:
            return self
        tz = self._anomaly_tz()
        day = pytz.utc.localize(self.check_in).astimezone(tz).date()
        return self._day_segments_for(self.employee_id.id, day)

    def _recompute_day_anomalies(self):
        """Recalcule anomalies + déductions pour TOUS les segments des journées
        concernées : la position 1er/dernier segment d'une présence dépend de
        ses voisines, donc l'ajout/retrait d'un segment doit réévaluer le jour
        entier (sinon le 1er segment garde un faux « départ anticipé »)."""
        if self.env.context.get('anomaly_skip_recompute'):
            return
        days = set()
        for att in self:
            if att.check_in and att.employee_id:
                tz = att._anomaly_tz()
                day = pytz.utc.localize(att.check_in).astimezone(tz).date()
                days.add((att.employee_id.id, day))
        recompute = self.with_context(anomaly_skip_recompute=True)
        for emp_id, day in days:
            segs = recompute._day_segments_for(emp_id, day)
            for att in segs:
                triggered, late_min, early_min = att._detect_anomalies()
                att.anomaly_ids = [(6, 0, triggered.ids)]
                self.env.cr.execute("""
                    UPDATE hr_attendance
                    SET is_anomaly = %s, anomaly_count = %s,
                        late_minutes = %s, early_leave_minutes = %s
                    WHERE id = %s
                """, (bool(triggered), len(triggered), late_min, early_min, att.id))
                att._sync_deduction_lines(late_min, early_min)
            segs.invalidate_recordset(
                ['is_anomaly', 'anomaly_count', 'late_minutes', 'early_leave_minutes'])

    @api.depends('check_in', 'check_out', 'scheduled_start', 'scheduled_end', 'checkout_only')
    def _compute_anomalies(self):
        for attendance in self:
            triggered, late_min, early_min = attendance._detect_anomalies()
            attendance.is_anomaly = bool(triggered)
            attendance.anomaly_count = len(triggered)
            attendance.late_minutes = late_min
            attendance.early_leave_minutes = early_min

    def _update_anomaly_ids(self):
        """Met à jour anomaly_ids selon les règles déclenchées (robuste à l'install)."""
        self.env.cr.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'hr_attendance_anomaly_type'
            )
        """)
        if not self.env.cr.fetchone()[0]:
            return
        for attendance in self:
            triggered, _lm, _em = attendance._detect_anomalies()
            attendance.anomaly_ids = [(6, 0, triggered.ids)]

    def _fill_scheduled_times(self):
        """Remplit scheduled_start / scheduled_end depuis le calendrier de l'employé.

        Gère Ramadan (hr_ramadan_schedule, optionnel via getattr) et les postes de
        nuit (convention Odoo hour_to>=24 ou wrap hour_to<hour_from).
        """
        for attendance in self:
            if attendance.scheduled_start and attendance.scheduled_end:
                continue
            if not attendance.check_in or not attendance.employee_id:
                continue

            employee = attendance.employee_id
            calendar = None
            if employee.resource_id and employee.resource_id.calendar_id:
                calendar = employee.resource_id.calendar_id
            if not calendar:
                continue

            check_date = attendance.check_in.date()
            dayofweek = str(check_date.weekday())
            day_lines = calendar.attendance_ids.filtered(
                lambda l: l.dayofweek == dayofweek)
            if not day_lines:
                continue  # jour non travaillé (week-end)

            try:
                tz = pytz.timezone(calendar.tz or 'Africa/Algiers')
                first_line = min(day_lines, key=lambda l: l.hour_from)
                last_line = max(day_lines, key=lambda l: l.hour_to)
                hour_from = first_line.hour_from
                hour_to = last_line.hour_to

                # ── Override Ramadan (optionnel) ──
                if getattr(calendar, 'is_ramadan', False):
                    r_from = getattr(calendar, 'ramadan_hour_from', 0.0)
                    r_to = getattr(calendar, 'ramadan_hour_to', 0.0)
                    if r_to:
                        if getattr(calendar, 'ramadan_mode', 'uniform') == 'gender':
                            sex = getattr(employee, 'sex', 'male') or 'male'
                            if sex == 'female':
                                f_from = getattr(calendar, 'ramadan_hour_from_female', 0.0)
                                f_to = getattr(calendar, 'ramadan_hour_to_female', 0.0)
                                hour_to = f_to if f_to else r_to
                                hour_from = f_from if f_from else (r_from or hour_from)
                            else:
                                hour_to = r_to
                                if r_from:
                                    hour_from = r_from
                        else:
                            hour_to = r_to
                            if r_from:
                                hour_from = r_from

                start_h, start_m = int(hour_from), int((hour_from % 1) * 60)
                local_start = tz.localize(datetime(
                    check_date.year, check_date.month, check_date.day,
                    start_h, start_m, 0))

                if hour_to >= 24:
                    effective_to = hour_to - 24
                    end_date = check_date + timedelta(days=1)
                else:
                    effective_to = hour_to
                    end_date = check_date
                end_h = int(effective_to)
                end_m = int((effective_to % 1) * 60)
                local_end = tz.localize(datetime(
                    end_date.year, end_date.month, end_date.day,
                    end_h, end_m, 0))
                if local_end <= local_start:
                    local_end += timedelta(days=1)

                attendance.scheduled_start = local_start.astimezone(pytz.UTC).replace(tzinfo=None)
                attendance.scheduled_end = local_end.astimezone(pytz.UTC).replace(tzinfo=None)
            except Exception as e:
                _logger.warning(
                    "Calcul des heures prévues impossible (présence %s) : %s",
                    attendance.id, e)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._fill_scheduled_times()
        records._recompute_day_anomalies()
        return records

    def write(self, vals):
        if 'check_in' in vals:
            vals = dict(vals, scheduled_start=False, scheduled_end=False)
        res = super().write(vals)
        if 'check_in' in vals:
            self._fill_scheduled_times()
        if any(f in vals for f in ['check_in', 'check_out', 'scheduled_start', 'scheduled_end']):
            self._recompute_day_anomalies()
        return res

    def unlink(self):
        # Mémorise les journées touchées pour réévaluer les segments restants.
        affected = set()
        for att in self:
            if att.check_in and att.employee_id:
                tz = att._anomaly_tz()
                day = pytz.utc.localize(att.check_in).astimezone(tz).date()
                affected.add((att.employee_id.id, day))
        res = super().unlink()
        Att = self.browse()
        for emp_id, day in affected:
            segs = Att._day_segments_for(emp_id, day)
            if segs:
                segs._recompute_day_anomalies()
        return res

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_fill_scheduled_times(self):
        self.write({'scheduled_start': False, 'scheduled_end': False})
        self._fill_scheduled_times()
        self._update_anomaly_ids()
        return self._notify(_('Heures prévues remplies pour %d présence(s)') % len(self))

    def action_recompute_anomalies(self):
        """Recalcule anomalies + déductions sur la sélection (ou le domaine actif)."""
        if self.ids:
            targets = self
        else:
            targets = self.search(self.env.context.get('active_domain', []))
        targets._fill_scheduled_times()

        anomaly_count = 0
        for att in targets:
            triggered, late_min, early_min = att._detect_anomalies()
            att.anomaly_ids = [(6, 0, triggered.ids)]
            self.env.cr.execute("""
                UPDATE hr_attendance
                SET is_anomaly = %s, anomaly_count = %s,
                    late_minutes = %s, early_leave_minutes = %s
                WHERE id = %s
            """, (bool(triggered), len(triggered), late_min, early_min, att.id))
            att._sync_deduction_lines(late_min, early_min)
            if triggered:
                anomaly_count += 1
        targets.invalidate_recordset()

        return self._notify(
            _('%d présence(s) analysée(s) — %d anomalie(s) détectée(s)') % (
                len(targets), anomaly_count),
            success=bool(anomaly_count))

    def action_validate_deductions(self):
        self.deduction_ids.filtered(lambda d: d.status == 'to_validate').action_validate()

    def action_refuse_deductions(self):
        self.deduction_ids.filtered(lambda d: d.status == 'to_validate').action_refuse()

    @api.model
    def _notify(self, message, success=True):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Présences'),
                'message': message,
                'type': 'success' if success else 'warning',
                'sticky': False,
            },
        }