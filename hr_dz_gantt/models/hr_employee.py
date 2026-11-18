from collections import defaultdict
from datetime import timedelta

import pytz

from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _dz_gantt_unavailability(self, start, stop):
        """Périodes où l'employé ne travaille pas, entre start et stop (UTC naïfs).

        Le « prévu » est celui du calcul des prestations
        (`hr.dz.work.entry.engine._planned_intervals`, planning du contrat
        du jour : deux semaines, poste de nuit, Ramadan…) ; s'y ajoutent les
        jours fériés et les absences de ressource (congés validés). Un employé
        à horaires flexibles, ou sans planning, n'a rien de grisé.

        Calcul groupé : versions des contrats lues une fois par employé,
        créneaux d'un planning calculés une fois par jour, absences de tous
        les employés en une seule requête.

        :return: {employee_id: [{'start': 'AAAA-MM-JJ HH:MM:SS', 'stop': ...}]} en UTC
        """
        if not start or not stop or not self:
            return {}
        engine = self.env['hr.dz.work.entry.engine']
        start_utc = start.replace(tzinfo=None)
        stop_utc = stop.replace(tzinfo=None)
        employees = self.sudo()

        leaves_by_resource = defaultdict(list)
        global_leaves = []
        for leave in self.env['resource.calendar.leaves'].sudo().search([
            ('date_from', '<', stop_utc), ('date_to', '>', start_utc),
            '|', ('resource_id', '=', False), ('resource_id', 'in', employees.resource_id.ids),
        ]):
            span = (max(leave.date_from, start_utc), min(leave.date_to, stop_utc))
            if leave.resource_id:
                leaves_by_resource[leave.resource_id.id].append(span)
            else:
                global_leaves.append((leave.company_id.id, span))

        planned_cache = {}

        def planned(employee, calendar, day):
            key = (calendar.id, day, getattr(employee, 'sex', False))
            if key not in planned_cache:
                planned_cache[key] = engine._planned_intervals(employee, calendar, day)
            return planned_cache[key]

        result = {}
        for employee in employees:
            versions = employee.version_ids.sorted('date_version')

            def version_at(day):
                current = versions[:1]
                for version in versions:
                    if version.date_version and version.date_version <= day:
                        current = version
                return current

            tz = engine._tz(employee)
            day = pytz.utc.localize(start_utc).astimezone(tz).date() - timedelta(days=1)
            last_day = pytz.utc.localize(stop_utc).astimezone(tz).date()
            work = []
            flexible = False
            while day <= last_day:
                calendar = engine._calendar(employee, version_at(day))
                if not calendar or getattr(calendar, 'flexible_hours', False):
                    flexible = True
                    break
                for begin, end in planned(employee, calendar, day):
                    work.append((tz.localize(begin).astimezone(pytz.utc).replace(tzinfo=None),
                                 tz.localize(end).astimezone(pytz.utc).replace(tzinfo=None)))
                day += timedelta(days=1)
            if flexible:
                continue
            gaps = []
            cursor = start_utc
            for begin, end in sorted(work):
                if begin > cursor:
                    gaps.append((cursor, min(begin, stop_utc)))
                cursor = max(cursor, end)
                if cursor >= stop_utc:
                    break
            if cursor < stop_utc:
                gaps.append((cursor, stop_utc))
            gaps += leaves_by_resource.get(employee.resource_id.id, [])
            gaps += [span for company_id, span in global_leaves
                     if not company_id or company_id == employee.company_id.id]
            result[employee.id] = [
                {'start': fields.Datetime.to_string(b), 'stop': fields.Datetime.to_string(e)}
                for b, e in sorted(gaps) if e > b]
        return result
