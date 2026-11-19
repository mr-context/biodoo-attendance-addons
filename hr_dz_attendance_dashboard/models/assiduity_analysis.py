# -*- coding: utf-8 -*-
"""
Analyse d'assiduité individuelle (fiche employé → bouton « Assiduité »).

Rien n'est recalculé ici : l'analyse LIT ce que biodoo a déjà établi.
- Le classement de chaque journée (présent, absent, congé, férié, pointage
  incomplet…) vient de ``hr.dz.work.entry.engine.day_facts`` : la même lecture
  que les prestations et la paie, donc l'analyse ne peut pas les contredire.
- Retards et départs anticipés : champs stockés de hr_dz_attendance_anomaly
  (``late_minutes``, ``early_leave_minutes``, ``anomaly_ids``), qui appliquent
  déjà la politique de présence (tolérances, segments, pauses en cours).
- Retenues : ``hr.attendance.deduction``.
- Corrections manuelles : suivi des modifications (mail.tracking.value) des
  heures d'entrée / sortie, plus les présences saisies à la main.

Ce que l'analyse ajoute : agrégats, tendances, comparaison avec le service,
score d'assiduité et signaux d'alerte (seuils dans la société, voir
res_company.py). Aucun champ stocké, aucun cron.
"""

from collections import defaultdict
from datetime import datetime, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools.misc import format_date

# Jours ouvrés d'un mois type : au-delà, les pénalités du score sont ramenées
# à un mois pour qu'un score annuel reste comparable à un score mensuel.
MONTH_WORK_DAYS = 22
TREND_MONTHS = 12

PRESENT_STATUSES = ('present', 'missing_punch')
EXPECTED_STATUSES = ('present', 'missing_punch', 'absent')

STATUS_LABELS = {
    'off': 'Repos',
    'holiday': 'Férié',
    'leave': 'Congé',
    'present': 'Présent',
    'missing_punch': 'Pointage incomplet',
    'absent': 'Absence injustifiée',
    'pending': 'À constater',
}


class HrDzAssiduityAnalysis(models.AbstractModel):
    _name = 'hr.dz.assiduity.analysis'
    _description = "Analyse d'assiduité d'un employé"

    # ── Outils ───────────────────────────────────────────────────────────────

    @api.model
    def _engine(self):
        return self.env['hr.dz.work.entry.engine']

    @api.model
    def _today(self, employee):
        return datetime.now(self._engine()._tz(employee)).date()

    @api.model
    def _default_period(self, employee):
        today = self._today(employee)
        return today.replace(day=1), today

    @api.model
    def _score_config(self, company):
        return {
            'late': company.dz_score_late_penalty,
            'early': company.dz_score_early_penalty,
            'absent': company.dz_score_absence_penalty,
            'missing': company.dz_score_missing_penalty,
            'break': company.dz_score_break_penalty,
            'good': company.dz_score_good_threshold,
            'warning': company.dz_score_warning_threshold,
            'late_alert_count': company.dz_alert_late_count,
            'tolerance_alert_count': company.dz_alert_tolerance_count,
            'break_alert_count': company.dz_alert_break_count,
        }

    @staticmethod
    def _local(dt_utc, tz):
        return pytz.utc.localize(dt_utc).astimezone(tz).replace(tzinfo=None) if dt_utc else None

    @staticmethod
    def _hm(dt):
        return dt.strftime('%H:%M') if dt else ''

    # ── Une journée ──────────────────────────────────────────────────────────

    # ── Pause déjeuner ───────────────────────────────────────────────────────

    @api.model
    def _lunch_window(self, employee, fact):
        """Pause prévue ce jour (début, fin) en heure locale naïve, ou None.

        La ligne « pause déjeuner » du planning si elle existe (celle dont se
        sert zkteco_connector pour la durée allouée), sinon le trou entre les
        deux premiers créneaux prévus par le moteur.
        """
        intervals = fact['intervals']
        if len(intervals) < 2:
            return None     # journée continue, Ramadan, horaires flexibles
        calendar = self._engine()._calendar(employee, fact['version'])
        day = fact['day']
        lines = calendar.attendance_ids.filtered(
            lambda a: a.dayofweek == str(day.weekday()) and a.day_period == 'lunch')
        if calendar.two_weeks_calendar:
            week_type = str(self.env['resource.calendar.attendance'].get_week_type(day))
            lines = lines.filtered(lambda a: a.week_type == week_type)
        if lines:
            midnight = datetime.combine(day, datetime.min.time())
            line = lines[0]
            return (midnight + timedelta(hours=line.hour_from),
                    midnight + timedelta(hours=line.hour_to))
        return intervals[0][1], intervals[1][0]

    @api.model
    def _break_tolerance(self, employee, fact, policy):
        """Tolérance de pause (min) : celle du planning (zkteco_connector) si
        le champ existe, sinon la tolérance de retard de la politique."""
        calendar = self._engine()._calendar(employee, fact['version'])
        if calendar and 'zkteco_break_tolerance' in calendar._fields:
            return calendar.zkteco_break_tolerance or 0
        return policy.late_tolerance_minutes

    @api.model
    def _day_break(self, employee, fact, tz, policy, device_breaks):
        """Pause réelle de la journée comparée à la pause prévue.

        - Pause pointée sur la pointeuse (mode « pause sortie / retour ») :
          on reprend zkteco.attendance.break tel quel (durée, allouée,
          tolérance, conformité) — un seul calcul dans biodoo.
        - Sinon, journée pointée en au moins deux segments : la pause est le
          trou entre la sortie du 1er segment et l'entrée du 2e.
        - Un seul segment (2 pointages) : pause non mesurable, rien d'inventé.
        """
        window = self._lunch_window(employee, fact)
        tol = self._break_tolerance(employee, fact, policy)
        if device_breaks:
            done = device_breaks.filtered(lambda b: b.break_end)
            if not done:
                return None
            b = done.sorted('break_start')[0]
            real_start, real_end = self._local(b.break_start, tz), self._local(b.break_end, tz)
            real = round(sum(done.mapped('duration_real')) * 60)
            planned = round(b.duration_allowed * 60)
            over = round(sum(done.mapped('deviation')) * 60)
            tol = round(b.tolerance * 60)
            is_over = any(x.compliance == 'over' for x in done)
            ids = done.mapped('attendance_id').ids
        else:
            atts = fact['attendances'].sorted('check_in')
            if len(atts) < 2 or not atts[0].check_out or not window:
                return None
            real_start, real_end = self._local(atts[0].check_out, tz), self._local(atts[1].check_in, tz)
            real = round((real_end - real_start).total_seconds() / 60)
            planned = round((window[1] - window[0]).total_seconds() / 60)
            over = real - planned
            is_over = over > tol
            ids = atts[1].ids
        shifted = bool(window) and not is_over and (
            abs((real_start - window[0]).total_seconds()) / 60 > tol
            or (real_end - window[1]).total_seconds() / 60 > tol)
        return {
            'start': self._hm(real_start),
            'end': self._hm(real_end),
            'minutes': real,
            'planned': planned,
            'over': max(0, over) if is_over else 0,
            'shifted': shifted,
            'window': '%s–%s' % (self._hm(window[0]), self._hm(window[1])) if window else '',
            'attendance_ids': ids,
        }

    @api.model
    def _day_row(self, employee, fact, tz, policy, device_breaks):
        """Fait du moteur → ligne d'analyse (types simples, sérialisable)."""
        tolerance = policy.late_tolerance_minutes
        brk = None
        if fact['status'] in PRESENT_STATUSES:
            brk = self._day_break(employee, fact, tz, policy, device_breaks)
        atts = fact['attendances']
        intervals = fact['intervals']
        sched_start = intervals[0][0] if intervals else None
        sched_end = max(i[1] for i in intervals) if intervals else None
        first_in = self._local(min(atts.mapped('check_in')), tz) if atts else None
        outs = [a.check_out for a in atts if a.check_out]
        last_out = self._local(max(outs), tz) if outs else None
        late_min = sum(atts.mapped('late_minutes'))
        early_min = sum(atts.mapped('early_leave_minutes'))
        offset = None
        if first_in and sched_start:
            offset = round((first_in - sched_start).total_seconds() / 60)
        codes = set(atts.mapped('anomaly_ids.code'))
        note = ''
        if fact['holiday']:
            note = fact['holiday'].name or ''
        elif fact['leave']:
            note = fact['leave'].holiday_status_id.name or ''
        return {
            'day': fact['day'],
            'status': fact['status'],
            'planned': round(fact['planned'], 2),
            'worked': round(sum(atts.mapped('worked_hours')), 2),
            'late_min': late_min,
            'early_min': early_min,
            'offset': offset,
            'tolerance_edge': bool(late_min == 0 and offset is not None and 0 < offset <= tolerance),
            'suspect': 'early_checkin' in codes,
            'anomalies': sorted(set(atts.mapped('anomaly_ids.name'))),
            'overtime': round(sum(fact['overtime'].values()), 2),
            'sched': '%s–%s' % (self._hm(sched_start), self._hm(sched_end)) if sched_start else '',
            'check_in': self._hm(first_in),
            'check_out': self._hm(last_out),
            'attendance_ids': atts.ids,
            'late_ids': atts.filtered(lambda a: a.late_minutes > 0).ids,
            'early_ids': atts.filtered(lambda a: a.early_leave_minutes > 0).ids,
            'incomplete_ids': atts.filtered(lambda a: not a.check_out).ids,
            'suspect_ids': atts.filtered(lambda a: 'early_checkin' in a.anomaly_ids.mapped('code')).ids,
            'break': brk,
            'break_over': brk['over'] if brk else 0,
            'note': note,
        }

    @api.model
    def _rows(self, employee, date_from, date_to):
        tz = self._engine()._tz(employee)
        policy = employee._dz_attendance_policy()
        # Pauses pointées (zkteco_connector) — couplage souple.
        breaks = {}
        if 'zkteco.attendance.break' in self.env:
            for b in self.env['zkteco.attendance.break'].sudo().search([
                    ('employee_id', '=', employee.id), ('date', '>=', date_from),
                    ('date', '<=', date_to), ('source', '=', 'device')]):
                breaks[b.date] = breaks.get(b.date, b.browse()) | b
        return [self._day_row(employee, f, tz, policy, breaks.get(f['day']))
                for f in self._engine().day_facts(employee, date_from, date_to)]

    # ── Agrégats d'une période ───────────────────────────────────────────────

    @api.model
    def _summarise(self, rows, cfg):
        expected = [r for r in rows if r['status'] in EXPECTED_STATUSES and r['planned'] > 0]
        present = [r for r in expected if r['status'] in PRESENT_STATUSES]
        late = [r for r in rows if r['late_min'] > 0]
        early = [r for r in rows if r['early_min'] > 0]
        absent = [r for r in rows if r['status'] == 'absent']
        missing = [r for r in rows if r['status'] == 'missing_punch']
        planned_hours = sum(r['planned'] for r in expected)
        worked_hours = sum(r['worked'] for r in rows)
        late_minutes = sum(r['late_min'] for r in late)
        summary = {
            'expected_days': len(expected),
            'present_days': len(present),
            'presence_rate': round(100.0 * len(present) / len(expected), 1) if expected else None,
            'absent_count': len(absent),
            'missing_count': len(missing),
            'leave_days': sum(1 for r in rows if r['status'] == 'leave'),
            'holiday_days': sum(1 for r in rows if r['status'] == 'holiday'),
            'rest_worked_days': sum(1 for r in rows
                                    if r['status'] in PRESENT_STATUSES and r['planned'] <= 0),
            'late_count': len(late),
            'late_minutes': late_minutes,
            'late_avg': round(late_minutes / len(late)) if late else 0,
            'late_max': max((r['late_min'] for r in late), default=0),
            'early_count': len(early),
            'early_minutes': sum(r['early_min'] for r in early),
            'planned_hours': round(planned_hours, 2),
            'worked_hours': round(worked_hours, 2),
            'balance_hours': round(worked_hours - planned_hours, 2),
            'overtime_hours': round(sum(r['overtime'] for r in rows), 2),
            'tolerance_edge_count': sum(1 for r in rows if r['tolerance_edge']),
            'suspect_count': sum(1 for r in rows if r['suspect']),
            'break_days': sum(1 for r in rows if r['break']),
            'break_over_count': sum(1 for r in rows if r['break_over']),
            'break_over_minutes': sum(r['break_over'] for r in rows),
            'break_shift_count': sum(1 for r in rows if r['break'] and r['break']['shifted']),
        }
        summary.update(self._score(summary, cfg))
        return summary

    @api.model
    def _score(self, s, cfg):
        """Score sur 100 : 100 moins les pénalités de la société.

        Jusqu'à un mois de jours ouvrés, les pénalités comptent telles quelles ;
        au-delà, elles sont ramenées à un mois type (MONTH_WORK_DAYS), sinon un
        score annuel tomberait toujours à 0 et ne se comparerait à rien.
        """
        if not s['expected_days']:
            return {'score': None, 'level': 'none'}
        penalty = (s['late_count'] * cfg['late'] + s['early_count'] * cfg['early']
                   + s['absent_count'] * cfg['absent'] + s['missing_count'] * cfg['missing']
                   + s.get('break_over_count', 0) * cfg['break'])
        if s['expected_days'] > MONTH_WORK_DAYS:
            penalty = penalty * MONTH_WORK_DAYS / s['expected_days']
        score = max(0, min(100, round(100 - penalty)))
        level = ('good' if score >= cfg['good']
                 else 'warning' if score >= cfg['warning'] else 'danger')
        return {'score': score, 'level': level}

    @api.model
    def period_summary(self, employee, date_from=None, date_to=None):
        """Agrégats seuls (bouton de la fiche employé) : une seule lecture du mois."""
        if not date_from or not date_to:
            date_from, date_to = self._default_period(employee)
        return self._summarise(self._rows(employee, date_from, date_to),
                               self._score_config(employee.company_id))

    # ── Analyse complète ─────────────────────────────────────────────────────

    @api.model
    def analyse(self, employee, date_from=None, date_to=None):
        employee.ensure_one()
        today = self._today(employee)
        if not date_from or not date_to:
            date_from, date_to = self._default_period(employee)
        date_to = min(date_to, today)
        if date_from > date_to:
            date_from = date_to
        length = (date_to - date_from).days + 1
        prev_to = date_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=length - 1)
        trend_from = date_to.replace(day=1) - relativedelta(months=TREND_MONTHS - 1)
        # Fenêtre glissante de 30 jours pour l'alerte « N retards en 30 jours ».
        range_from = min(prev_from, trend_from, date_from - timedelta(days=29))

        # Une seule lecture du moteur pour toute l'étendue, puis découpage.
        all_rows = self._rows(employee, range_from, date_to)
        by_day = {r['day']: r for r in all_rows}
        rows = [r for r in all_rows if date_from <= r['day'] <= date_to]
        prev_rows = [r for r in all_rows if prev_from <= r['day'] <= prev_to]
        cfg = self._score_config(employee.company_id)
        summary = self._summarise(rows, cfg)
        previous = self._summarise(prev_rows, cfg)
        # Période précédente comparable : au moins la moitié des jours attendus
        # (sinon un nouvel embauché « explose » toujours face à une période vide).
        previous['comparable'] = previous['expected_days'] >= summary['expected_days'] / 2
        policy = employee._dz_attendance_policy()

        corrections = self._corrections(employee, rows)
        deductions = self._deductions(employee, date_from, date_to)
        breaks = self._break_issues(employee, date_from, date_to)
        summary['correction_count'] = len(corrections)
        summary['break_issue_count'] = len(breaks)

        return {
            'employee': self._employee_info(employee, policy),
            'period': {
                'date_from': date_from.isoformat(),
                'date_to': date_to.isoformat(),
                'prev_from': prev_from.isoformat(),
                'prev_to': prev_to.isoformat(),
                'label': '%s → %s' % (format_date(self.env, date_from), format_date(self.env, date_to)),
                'today': today.isoformat(),
            },
            'config': cfg,
            'summary': summary,
            'previous': previous,
            'alerts': self._alerts(rows, by_day, summary, previous, cfg, corrections, deductions),
            'trend': self._trend(all_rows, trend_from, date_to, cfg),
            'weekdays': self._weekdays(rows),
            'arrivals': [{'day': r['day'].isoformat(), 'offset': r['offset'], 'late': r['late_min'] > 0}
                         for r in rows if r['offset'] is not None],
            'calendar': [self._calendar_cell(r) for r in rows],
            'journal': [self._journal_line(r) for r in reversed(rows)
                        if r['status'] != 'off' or r['attendance_ids']],
            'comparison': self._comparison(employee, date_from, date_to),
            'corrections': corrections,
            'deductions': deductions,
            'drill': {
                'attendance_ids': [i for r in rows for i in r['attendance_ids']],
                'late_ids': [i for r in rows for i in r['late_ids']],
                'early_ids': [i for r in rows for i in r['early_ids']],
                'incomplete_ids': [i for r in rows if r['status'] == 'missing_punch'
                                   for i in r['incomplete_ids']],
                'suspect_ids': [i for r in rows for i in r['suspect_ids']],
                'absent_days': [r['day'].isoformat() for r in rows if r['status'] == 'absent'],
                'correction_ids': sorted({c['attendance_id'] for c in corrections}),
                'break_ids': breaks,
                'break_over_ids': [i for r in rows if r['break_over']
                                   for i in r['break']['attendance_ids']],
                'deduction_ids': deductions['ids'],
            },
        }

    # ── Blocs de l'analyse ───────────────────────────────────────────────────

    @api.model
    def _employee_info(self, employee, policy):
        return {
            'id': employee.id,
            'name': employee.name,
            'job': employee.job_title or employee.job_id.name or '',
            'department': employee.department_id.name or '',
            'calendar': employee.resource_calendar_id.name or '',
            'policy': policy.name or '',
            'late_tolerance': policy.late_tolerance_minutes,
            'early_tolerance': policy.early_tolerance_minutes,
        }

    @api.model
    def _calendar_cell(self, r):
        return {
            'day': r['day'].isoformat(),
            'status': r['status'],
            'label': STATUS_LABELS.get(r['status'], r['status']),
            'late_min': r['late_min'],
            'early_min': r['early_min'],
            'worked': r['worked'],
            'note': r['note'],
            'attendance_ids': r['attendance_ids'],
        }

    @api.model
    def _journal_line(self, r):
        return {
            'day': r['day'].isoformat(),
            'day_label': format_date(self.env, r['day'], date_format='EEE dd/MM/yyyy'),
            'weekday': r['day'].weekday(),
            'status': r['status'],
            'label': STATUS_LABELS.get(r['status'], r['status']),
            'sched': r['sched'],
            'check_in': r['check_in'],
            'check_out': r['check_out'],
            'worked': r['worked'],
            'planned': r['planned'],
            'late_min': r['late_min'],
            'early_min': r['early_min'],
            'anomalies': r['anomalies'],
            'break': r['break'],
            'note': r['note'],
            'attendance_ids': r['attendance_ids'],
        }

    @api.model
    def _trend(self, all_rows, trend_from, date_to, cfg):
        by_month = defaultdict(list)
        for r in all_rows:
            if r['day'] >= trend_from:
                by_month[r['day'].replace(day=1)].append(r)
        trend = []
        month = trend_from
        while month <= date_to:
            s = self._summarise(by_month.get(month, []), cfg)
            trend.append({
                'month': month.isoformat(),
                'label': format_date(self.env, month, date_format='MMM yy'),
                'score': s['score'],
                'presence_rate': s['presence_rate'],
                'late_count': s['late_count'],
                'late_minutes': s['late_minutes'],
                'absent_count': s['absent_count'],
                'early_count': s['early_count'],
                'break_over_count': s['break_over_count'],
            })
            month += relativedelta(months=1)
        return trend

    @api.model
    def _weekdays(self, rows):
        stats = {wd: {'weekday': wd, 'late_count': 0, 'late_minutes': 0, 'early_count': 0,
                      'absent_count': 0, 'break_over_count': 0, 'offset_sum': 0, 'offset_n': 0}
                 for wd in range(7)}
        for r in rows:
            s = stats[r['day'].weekday()]
            if r['late_min'] > 0:
                s['late_count'] += 1
                s['late_minutes'] += r['late_min']
            if r['early_min'] > 0:
                s['early_count'] += 1
            if r['status'] == 'absent':
                s['absent_count'] += 1
            if r['break_over']:
                s['break_over_count'] += 1
            if r['offset'] is not None and r['planned'] > 0:
                s['offset_sum'] += r['offset']
                s['offset_n'] += 1
        result = []
        for wd in range(7):
            s = stats[wd]
            n = s.pop('offset_n')
            total = s.pop('offset_sum')
            s['offset_avg'] = round(total / n) if n else None
            result.append(s)
        return result

    @api.model
    def _comparison(self, employee, date_from, date_to):
        """L'employé face à son service (à défaut, sa société) sur la période.

        Mêmes champs stockés pour lui et pour ses collègues : la comparaison
        est à armes égales. Moyenne sur les collègues qui ont pointé au moins
        une fois sur la période (un absent de longue durée ne la fausse pas).
        """
        Employee = self.env['hr.employee'].sudo()
        if employee.department_id:
            scope = employee.department_id.name
            peers = Employee.search([('department_id', '=', employee.department_id.id)])
        else:
            scope = employee.company_id.name
            peers = Employee.search([('company_id', '=', employee.company_id.id)])
        tz = self._engine()._tz(employee)
        utc_from, utc_to = self._engine()._utc_bounds(date_from, date_to, tz)
        base = [('employee_id', 'in', peers.ids),
                ('check_in', '>=', utc_from), ('check_in', '<', utc_to)]
        Attendance = self.env['hr.attendance'].sudo()
        stats = defaultdict(lambda: {'late_count': 0, 'late_minutes': 0, 'early_count': 0,
                                     'early_minutes': 0, 'worked_hours': 0.0})
        for emp, worked, late_sum, early_sum in Attendance._read_group(
                base, ['employee_id'],
                ['worked_hours:sum', 'late_minutes:sum', 'early_leave_minutes:sum']):
            s = stats[emp.id]
            s['worked_hours'] = worked or 0.0
            s['late_minutes'] = late_sum or 0
            s['early_minutes'] = early_sum or 0
        for emp, count in Attendance._read_group(
                base + [('late_minutes', '>', 0)], ['employee_id'], ['__count']):
            stats[emp.id]['late_count'] = count
        for emp, count in Attendance._read_group(
                base + [('early_leave_minutes', '>', 0)], ['employee_id'], ['__count']):
            stats[emp.id]['early_count'] = count

        mine = dict(stats[employee.id]) if employee.id in stats else {
            'late_count': 0, 'late_minutes': 0, 'early_count': 0,
            'early_minutes': 0, 'worked_hours': 0.0}
        others = list(stats.values())
        keys = ('late_count', 'late_minutes', 'early_count', 'early_minutes', 'worked_hours')
        average = {k: (round(sum(s[k] for s in others) / len(others), 1) if others else 0)
                   for k in keys}
        # Rang : 1 = le plus en retard du service (minutes cumulées).
        ranking = sorted(stats.items(), key=lambda kv: kv[1]['late_minutes'], reverse=True)
        rank = next((i + 1 for i, (emp_id, _s) in enumerate(ranking) if emp_id == employee.id), None)
        mine['worked_hours'] = round(mine['worked_hours'], 1)
        return {
            'scope': scope,
            'peer_count': len(others),
            'employee': mine,
            'average': average,
            'late_rank': rank if mine['late_minutes'] > 0 else None,
        }

    @api.model
    def _corrections(self, employee, rows):
        """Heures d'entrée / sortie modifiées par une personne, et présences
        saisies à la main. Les écritures des pointeuses (OdooBot) n'en sont pas."""
        att_ids = [i for r in rows for i in r['attendance_ids']]
        if not att_ids:
            return []
        tz = self._engine()._tz(employee)
        root = self.env.ref('base.partner_root', raise_if_not_found=False)
        attendances = self.env['hr.attendance'].sudo().browse(att_ids)
        result = []
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', 'hr.attendance'),
            ('res_id', 'in', att_ids),
            ('tracking_value_ids', '!=', False),
        ] + ([('author_id', '!=', root.id)] if root else []), order='date desc')
        for msg in messages:
            att = attendances.browse(msg.res_id)
            for tv in msg.tracking_value_ids:
                fname = tv.field_id.name
                if fname not in ('check_in', 'check_out'):
                    continue
                # Sortie posée par l'employé lui-même (systray, kiosque) : ce
                # n'est pas une correction. Seule une sortie MANUELLE l'est.
                if not tv.old_value_datetime and not (
                        fname == 'check_out' and att.out_mode == 'manual'):
                    continue
                result.append({
                    'attendance_id': msg.res_id,
                    'when': fields.Datetime.to_string(self._local(msg.date, tz)),
                    'author': msg.author_id.name or '',
                    'what': 'Entrée' if fname == 'check_in' else 'Sortie',
                    'before': fields.Datetime.to_string(self._local(tv.old_value_datetime, tz))
                    if tv.old_value_datetime else '',
                    'after': fields.Datetime.to_string(self._local(tv.new_value_datetime, tz))
                    if tv.new_value_datetime else '',
                })
        tracked = {c['attendance_id'] for c in result}
        root_user = self.env.ref('base.user_root', raise_if_not_found=False)
        for att in attendances:
            if att.id in tracked or att.in_mode != 'manual':
                continue
            if root_user and att.create_uid == root_user:
                continue
            result.append({
                'attendance_id': att.id,
                'when': fields.Datetime.to_string(self._local(att.create_date, tz)),
                'author': att.create_uid.name or '',
                'what': 'Saisie manuelle',
                'before': '',
                'after': fields.Datetime.to_string(self._local(att.check_in, tz)),
            })
        result.sort(key=lambda c: c['when'], reverse=True)
        return result

    @api.model
    def _deductions(self, employee, date_from, date_to):
        deductions = self.env['hr.attendance.deduction'].sudo().search([
            ('employee_id', '=', employee.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ])
        by_status = defaultdict(int)
        for ded in deductions:
            by_status[ded.status] += 1
        validated = deductions.filtered(lambda d: d.status == 'validated')
        return {
            'ids': deductions.ids,
            'to_validate': by_status['to_validate'],
            'validated': by_status['validated'],
            'refused': by_status['refused'],
            'justified': by_status['justified'],
            'retained_hours': round(sum(validated.mapped('retained_hours')), 2),
        }

    @api.model
    def _break_issues(self, employee, date_from, date_to):
        """Pauses hors règle — couplage souple : sans zkteco_connector, rien."""
        if 'zkteco.attendance.break' not in self.env:
            return []
        return self.env['zkteco.attendance.break'].sudo().search([
            ('employee_id', '=', employee.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('compliance', 'in', ('over', 'under')),
        ]).ids

    @api.model
    def _alerts(self, rows, by_day, summary, previous, cfg, corrections, deductions):
        alerts = []

        def add(level, icon, title, detail='', days=None, drill=None):
            alerts.append({'level': level, 'icon': icon, 'title': title, 'detail': detail,
                           'days': [d.isoformat() for d in (days or [])], 'drill': drill})

        def fmt(days):
            return ', '.join(format_date(self.env, d, date_format='dd/MM') for d in days)

        absent = [r['day'] for r in rows if r['status'] == 'absent']
        if absent:
            add('danger', 'fa-user-times', '%d absence(s) injustifiée(s)' % len(absent),
                fmt(absent), absent, 'absent')

        # Retards en fenêtre glissante de 30 jours (fenêtre lue avant la période).
        late_days = sorted(d for d, r in by_day.items() if r['late_min'] > 0)
        worst, worst_end = 0, None
        for r in rows:
            window = [d for d in late_days if r['day'] - timedelta(days=29) <= d <= r['day']]
            if len(window) > worst:
                worst, worst_end = len(window), r['day']
        if worst >= cfg['late_alert_count']:
            add('danger', 'fa-clock-o',
                '%d retards en 30 jours (seuil %d)' % (worst, cfg['late_alert_count']),
                'Fenêtre se terminant le %s' % format_date(self.env, worst_end), drill='late')

        # Absence collée à un repos, un férié ou un congé (« pont »).
        bridges = []
        for day in absent:
            neighbours = [by_day.get(day - timedelta(days=1)), by_day.get(day + timedelta(days=1))]
            if any(n and n['status'] in ('off', 'holiday', 'leave') for n in neighbours):
                bridges.append(day)
        if bridges:
            add('danger', 'fa-chain-broken',
                '%d absence(s) collée(s) à un repos, férié ou congé' % len(bridges),
                fmt(bridges), bridges, 'absent')

        # Retards qui s'aggravent par rapport à la période précédente comparable.
        if summary['late_count'] > previous['late_count'] and previous.get('comparable'):
            add('warning', 'fa-line-chart',
                'Retards en hausse : %d contre %d la période précédente'
                % (summary['late_count'], previous['late_count']),
                '%d min cumulées contre %d' % (summary['late_minutes'], previous['late_minutes']),
                drill='late')

        # Dérive de l'heure d'arrivée (pente des écarts à l'heure prévue).
        points = [((r['day'] - rows[0]['day']).days, r['offset'])
                  for r in rows if r['offset'] is not None and r['planned'] > 0]
        if len(points) >= 8:
            n = len(points)
            mx = sum(p[0] for p in points) / n
            my = sum(p[1] for p in points) / n
            var = sum((p[0] - mx) ** 2 for p in points)
            if var:
                slope_week = 7 * sum((p[0] - mx) * (p[1] - my) for p in points) / var
                if slope_week >= 2:
                    add('warning', 'fa-arrow-up',
                        "Arrive de plus en plus tard : +%.1f min par semaine" % slope_week,
                        "Tendance de l'heure d'arrivée sur la période")

        if summary['tolerance_edge_count'] >= cfg['tolerance_alert_count']:
            add('warning', 'fa-hourglass-end',
                '%d arrivée(s) juste dans la tolérance' % summary['tolerance_edge_count'],
                'En retard sur le planning, mais pas au-delà de la tolérance de la politique')

        if summary['break_over_count']:
            over_days = [r['day'] for r in rows if r['break_over']]
            level = 'danger' if summary['break_over_count'] >= cfg['break_alert_count'] else 'warning'
            add(level, 'fa-cutlery',
                '%d pause(s) déjeuner dépassée(s) — %d min en trop'
                % (summary['break_over_count'], summary['break_over_minutes']),
                fmt(over_days[-10:]), over_days, 'break_over')
        if summary['break_shift_count']:
            add('info', 'fa-exchange',
                '%d pause(s) prise(s) hors de l\'horaire prévu' % summary['break_shift_count'],
                'Durée respectée, mais sortie ou retour décalé par rapport au planning')

        if summary['missing_count']:
            add('warning', 'fa-sign-out', '%d pointage(s) incomplet(s)' % summary['missing_count'],
                'Entrée sans sortie sur une journée terminée', drill='incomplete')

        if summary['suspect_count']:
            add('warning', 'fa-user-secret',
                '%d arrivée(s) suspecte(s)' % summary['suspect_count'],
                "Plus de 30 min avant l'heure prévue : vérifier qui a pointé", drill='suspect')

        if summary['early_count']:
            add('warning', 'fa-sign-out', '%d départ(s) anticipé(s)' % summary['early_count'],
                '%d min cumulées' % summary['early_minutes'], drill='early')

        if corrections:
            add('info', 'fa-pencil',
                '%d pointage(s) corrigé(s) à la main' % len({c['attendance_id'] for c in corrections}),
                'Détail dans « Corrections manuelles »', drill='corrections')

        if deductions['to_validate']:
            add('info', 'fa-gavel', '%d retenue(s) en attente de décision' % deductions['to_validate'],
                drill='deductions')

        order = {'danger': 0, 'warning': 1, 'info': 2}
        alerts.sort(key=lambda a: order[a['level']])
        return alerts
