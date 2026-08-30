"""
Endpoint de calcul du tableau de bord Présences.

Tout est recalculé à la demande (aucun champ stocké, aucun cron) :
appelé au chargement de la vue et à chaque changement de date côté
client — voir static/src/js/attendance_dashboard.js.
"""

from datetime import date, datetime, time, timedelta

import pytz

from odoo import http
from odoo.http import request


class AttendanceDashboardController(http.Controller):

    def _company_tz(self, company):
        tz_name = company.partner_id.tz or 'Africa/Algiers'
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.timezone('Africa/Algiers')

    @http.route('/biodoo_attendance/dashboard_stats', type='jsonrpc', auth='user')
    def dashboard_stats(self, date_str=None, **kw):
        env = request.env
        if not env.user.has_group('hr_attendance.group_hr_attendance_officer'):
            return {'authorized': False}

        company = env.company
        tz = self._company_tz(company)

        d = date.fromisoformat(date_str) if date_str else date.today()
        start_local = tz.localize(datetime.combine(d, time.min))
        end_local = tz.localize(datetime.combine(d + timedelta(days=1), time.min))
        start_utc = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)

        Attendance = env['hr.attendance']
        Version = env['hr.version']
        Deduction = env['hr.attendance.deduction']

        today_attendances = Attendance.search([
            ('employee_id.company_id', '=', company.id),
            ('check_in', '>=', start_utc),
            ('check_in', '<', end_utc),
        ])
        present_employee_ids = set(today_attendances.mapped('employee_id').ids)

        contract_versions = Version.search([
            ('company_id', '=', company.id),
            ('state', '=', 'active'),
            ('contract_date_start', '<=', d),
            '|',
            ('contract_date_end', '=', False),
            ('contract_date_end', '>=', d),
        ])
        contract_employee_ids = set(contract_versions.mapped('employee_id').ids)
        absent_employee_ids = contract_employee_ids - present_employee_ids

        # Répartition par calendrier (chaque calendrier peut avoir son propre
        # régime de pause — cf. resource.calendar.zkteco_use_punch_state — donc
        # utile pour le RH de voir l'effectif/présence par régime, pas juste
        # un total société qui mélange tout).
        # On liste TOUS les calendriers de la société, même sans employé
        # assigné : un "0/0" est un signal utile ("shift créé, personne
        # dessus"), pas du bruit à masquer.
        calendars = {}
        for cal in env['resource.calendar'].search([
            ('company_id', 'in', [company.id, False]),
        ]):
            calendars[cal.id] = {
                'calendar_id': cal.id,
                'calendar_name': cal.name,
                'employee_ids': [],
                'present_employee_ids': [],
            }

        for emp in env['hr.employee'].browse(sorted(contract_employee_ids)):
            cal = emp.resource_calendar_id
            key = cal.id if cal else 0
            entry = calendars.setdefault(key, {
                'calendar_id': key,
                'calendar_name': cal.name if cal else 'Sans calendrier',
                'employee_ids': [],
                'present_employee_ids': [],
            })
            entry['employee_ids'].append(emp.id)
            if emp.id in present_employee_ids:
                entry['present_employee_ids'].append(emp.id)

        calendar_breakdown = [
            {
                'calendar_id': entry['calendar_id'],
                'calendar_name': entry['calendar_name'],
                'contract_count': len(entry['employee_ids']),
                'present_count': len(entry['present_employee_ids']),
                'employee_ids': entry['employee_ids'],
            }
            for entry in calendars.values()
        ]
        calendar_breakdown.sort(key=lambda e: e['calendar_name'])

        late_attendances = today_attendances.filtered(
            lambda a: 'late' in a.anomaly_ids.mapped('code'))
        early_attendances = today_attendances.filtered(
            lambda a: 'early_leave' in a.anomaly_ids.mapped('code'))
        open_attendances = today_attendances.filtered(lambda a: not a.check_out)

        # Statut des pointeuses — toujours "à l'instant présent", indépendant
        # de la date sélectionnée dans le bandeau (comme les alertes contrats).
        device_status = []
        if 'zkteco.device' in env.registry.models:
            # 'offline' est un état à part entière (cron _cron_mark_offline),
            # pas juste "non approuvé" — il faut le garder dans la liste,
            # sinon une pointeuse qui tombe disparaît au lieu de s'afficher
            # en rouge.
            devices = env['zkteco.device'].search(
                [('state', 'in', ('approved', 'offline'))], order='display_name')
            device_status = [{
                'id': dev.id,
                'name': dev.display_name,
                'serial_number': dev.serial_number,
                'is_online': dev.is_online,
                'last_seen': dev.last_seen.isoformat() if dev.last_seen else None,
                'user_count': dev.user_count,
            } for dev in devices]

        break_issue_ids = []
        if 'zkteco.attendance.break' in env.registry.models:
            break_issue_ids = env['zkteco.attendance.break'].search([
                ('employee_id.company_id', '=', company.id),
                ('date', '=', d),
                ('compliance', 'in', ('over', 'under')),
            ]).ids

        leave_ids = []
        if 'hr.leave' in env.registry.models:
            leave_ids = env['hr.leave'].search([
                ('employee_id.company_id', '=', company.id),
                ('state', '=', 'validate'),
                ('date_from', '<=', end_utc),
                ('date_to', '>=', start_utc),
            ]).ids

        # Cumul depuis toujours jusqu'à la date sélectionnée (incluse) — pas
        # le jour exact seul, pour ne rien perdre d'un arriéré, mais borné
        # par la date choisie pour rester cohérent avec les autres cartes.
        pending_deductions = Deduction.search([
            ('employee_id.company_id', '=', company.id),
            ('status', '=', 'to_validate'),
            ('date', '<=', d),
        ])

        present_count = len(present_employee_ids)
        contract_count = len(contract_employee_ids)

        # ── Alertes contrats : indépendantes de la date sélectionnée dans le
        # bandeau (toujours "aujourd'hui"), ce sont des rappels d'échéance,
        # pas une photo d'un jour passé. ────────────────────────────────────
        real_today = date.today()

        trial_versions = Version.search([
            ('company_id', '=', company.id),
            ('state', 'in', ('active', 'pending')),
            ('trial_date_end', '!=', False),
            ('trial_state', '!=', 'confirmed'),
        ])
        trial_alerts = trial_versions.filtered(
            lambda v: v.trial_warning_level in ('warning', 'danger'))

        cdd_alert_date = real_today + timedelta(days=30)
        cdd_alerts = Version.search([
            ('company_id', '=', company.id),
            ('state', '=', 'active'),
            ('is_cdd', '=', True),
            ('contract_date_end', '!=', False),
            ('contract_date_end', '>=', real_today),
            ('contract_date_end', '<=', cdd_alert_date),
        ])

        return {
            'authorized': True,
            'date': d.isoformat(),
            'contract_count': contract_count,
            'present_count': present_count,
            'absent_count': len(absent_employee_ids),
            'late_count': len(late_attendances),
            'early_count': len(early_attendances),
            'open_count': len(open_attendances),
            'leave_count': len(leave_ids),
            'pending_deductions': len(pending_deductions),
            'break_issue_count': len(break_issue_ids),
            'trial_alert_count': len(trial_alerts),
            'cdd_alert_count': len(cdd_alerts),
            # ids pour le drill-down au clic (aucun champ stocké, tout est
            # recalculé à chaque appel — cf. static/src/js/attendance_dashboard.js)
            'present_attendance_ids': today_attendances.ids,
            'absent_employee_ids': list(absent_employee_ids),
            'late_attendance_ids': late_attendances.ids,
            'early_attendance_ids': early_attendances.ids,
            'open_attendance_ids': open_attendances.ids,
            'leave_ids': leave_ids,
            'deduction_ids': pending_deductions.ids,
            'break_issue_ids': break_issue_ids,
            'trial_alert_ids': trial_alerts.ids,
            'cdd_alert_ids': cdd_alerts.ids,
            'calendar_breakdown': calendar_breakdown,
            'device_status': device_status,
            'device_online_count': sum(1 for dvc in device_status if dvc['is_online']),
        }
