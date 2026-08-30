"""
Extension native de hr.version pour ajouter la source 'attendance'.

Suit le pattern Odoo 19 pour l'extension de work_entry_source.
"""

from datetime import datetime, timedelta, time, date
from collections import defaultdict

from odoo import models, fields, api, _


class HrVersion(models.Model):
    _inherit = 'hr.version'

    # Étendre la sélection work_entry_source de façon native
    work_entry_source = fields.Selection(
        selection_add=[('attendance', 'Présences (Pointage)')],
        ondelete={'attendance': 'set default'},
    )

    def _is_work_entry_source_attendance(self):
        """Retourne True si la source est 'attendance'"""
        return self.work_entry_source == 'attendance'

    def _should_generate_work_entries(self):
        """Override pour gérer la source attendance"""
        if self._is_work_entry_source_attendance():
            return True
        return super()._should_generate_work_entries()

    def _get_work_entries_values(self, date_start, date_stop):
        """
        Override pour générer les work entries depuis les présences
        quand work_entry_source == 'attendance'.
        """
        if not self._is_work_entry_source_attendance():
            return super()._get_work_entries_values(date_start, date_stop)

        # Générer depuis les présences
        return self._get_work_entries_values_from_attendance(date_start, date_stop)

    def _get_work_entries_values_from_attendance(self, date_start, date_stop):
        """
        Génère les valeurs de work entries depuis hr.attendance + calendar leaves.

        Détecte dynamiquement:
        - Présences → type par défaut (Attendance)
        - Heures sup validées → type overtime
        - Jours spéciaux (fériés, etc.) → type configuré sur le calendar leave
        """
        self.ensure_one()
        vals_list = []

        Attendance = self.env['hr.attendance']

        # Types de work entry
        default_type_id = self._get_default_work_entry_type_id()
        overtime_type = self.env['hr.work.entry.type'].search(
            [('code', '=', 'OVERTIME')], limit=1)
        overtime_type_id = overtime_type.id if overtime_type else default_type_id

        # Convertir les dates en datetime si nécessaire
        if isinstance(date_start, date) and not isinstance(date_start, datetime):
            date_start = datetime.combine(date_start, time.min)
        if isinstance(date_stop, date) and not isinstance(date_stop, datetime):
            date_stop = datetime.combine(date_stop, time.max)

        # Récupérer les présences pour cet employé dans la période
        attendances = Attendance.search([
            ('employee_id', '=', self.employee_id.id),
            ('check_in', '>=', date_start),
            ('check_in', '<=', date_stop),
            ('check_out', '!=', False),
        ], order='check_in')

        # Grouper les présences par jour
        attendance_by_date = defaultdict(list)
        for att in attendances:
            work_date = att.check_in.date()
            attendance_by_date[work_date].append(att)

        # ── 1. Prestations de présence ────────────────────────────
        for work_date, day_attendances in attendance_by_date.items():
            total_hours = sum(att.worked_hours for att in day_attendances)
            overtime_hours = sum(
                att.validated_overtime_hours for att in day_attendances)
            normal_hours = total_hours - overtime_hours

            if normal_hours > 0:
                vals_list.append({
                    'name': _('Présence %s') % work_date.strftime('%d/%m/%Y'),
                    'date': work_date,
                    'duration': normal_hours,
                    'work_entry_type_id': default_type_id,
                    'employee_id': self.employee_id.id,
                    'version_id': self.id,
                    'company_id': self.company_id.id,
                })

            if overtime_hours > 0:
                vals_list.append({
                    'name': _('Heures sup. %s') % work_date.strftime('%d/%m/%Y'),
                    'date': work_date,
                    'duration': overtime_hours,
                    'work_entry_type_id': overtime_type_id,
                    'employee_id': self.employee_id.id,
                    'version_id': self.id,
                    'company_id': self.company_id.id,
                })

        # ── 2. Jours spéciaux (fériés, etc.) ─────────────────────
        calendar_leaves = self.env['resource.calendar.leaves'].search([
            ('resource_id', '=', False),
            ('date_from', '<=', date_stop),
            ('date_to', '>=', date_start),
            ('work_entry_type_id', '!=', False),
        ])

        if calendar_leaves and attendance_by_date:
            employee = self.employee_id
            calendar = employee.resource_id.calendar_id
            working_days = set(
                int(att.dayofweek) for att in calendar.attendance_ids
            ) if calendar else {0, 1, 2, 3, 6}

            for leave in calendar_leaves:
                # Vérifier calendrier
                if leave.calendar_id and calendar \
                        and leave.calendar_id.id != calendar.id:
                    continue

                leave_start = max(leave.date_from.date(), date_start.date())
                leave_end = min(leave.date_to.date(), date_stop.date())
                current = leave_start
                while current <= leave_end:
                    is_working_day = current.weekday() in working_days
                    has_attendance = current in attendance_by_date

                    # Weekend + pas présent → rien
                    if not is_working_day and not has_attendance:
                        current += timedelta(days=1)
                        continue

                    # Jour ouvrable sans présence → férié payé
                    # Weekend avec présence → férié (200%)
                    day_str = str(current.weekday())
                    day_lines = calendar.attendance_ids.filtered(
                        lambda a, d=day_str: a.dayofweek == d
                    ) if calendar else False
                    duration = sum(
                        l.hour_to - l.hour_from for l in day_lines
                    ) if day_lines else 8.0

                    vals_list.append({
                        'name': '%s %s' % (
                            leave.name, current.strftime('%d/%m/%Y')),
                        'date': current,
                        'duration': duration,
                        'work_entry_type_id': leave.work_entry_type_id.id,
                        'employee_id': employee.id,
                        'version_id': self.id,
                        'company_id': self.company_id.id,
                    })

                    current += timedelta(days=1)

        return vals_list

    def _get_attendance_work_entry_type(self, attendance):
        """
        Retourne le type de work entry pour une présence.
        Peut être surchargé pour des règles plus complexes (HS, nuit, etc.)
        """
        return self.env.ref('hr_work_entry.work_entry_type_attendance', raise_if_not_found=False)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def generate_work_entries_from_attendance(self, date_from, date_to):
        """
        Méthode publique pour générer les work entries depuis les présences.
        Utilise le mécanisme natif d'Odoo.
        """
        for employee in self:
            version = employee.current_version_id
            if version and version.work_entry_source == 'attendance':
                version.generate_work_entries(date_from, date_to, force=True)

        return True

    def action_generate_work_entries_attendance(self):
        """Action pour générer les work entries depuis l'interface employé"""
        today = fields.Date.today()
        date_from = today.replace(day=1)
        date_to = today

        return {
            'type': 'ir.actions.act_window',
            'name': _('Générer les prestations'),
            'res_model': 'hr.work.entry.compute.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_date_from': date_from,
                'default_date_to': date_to,
                'default_employee_ids': self.ids,
            },
        }
