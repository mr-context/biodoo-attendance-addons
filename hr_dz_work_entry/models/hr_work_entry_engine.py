"""
Moteur unique de calcul des prestations à partir des pointages.

Pour chaque employé dont la version (contrat) est en source « Présences », et
pour chaque jour de la période, le moteur compare ce qui était PRÉVU (planning)
à ce qui s'est PASSÉ (pointages, fériés, congés) et en déduit les prestations :

    jour pointé                          → Présence (+ heures sup. approuvées,
                                           au taux des règles d'heures sup. d'Odoo)
    férié                                → type du jour férié
    congé validé, jour ouvré sans pointage → type du congé
    jour ouvré terminé (délai de la politique), ni pointage, ni férié, ni congé
                                         → Absence non justifiée (ABSENT)
    entrée sans sortie                   → selon la politique (journée prévue,
                                           absence, ou temps pointé seul)

Règles de propriété (qui a le droit de modifier quoi) :
- le moteur ne crée, ne modifie et ne supprime QUE ses propres prestations
  (dz_origin = 'auto') encore non validées ;
- un jour où existe une prestation saisie ou corrigée par les RH
  (dz_origin = 'manual') ou une prestation validée appartient aux RH : le
  moteur n'y touche plus ;
- les prestations issues d'une retenue (dz_origin = 'deduction') sont gérées
  par le circuit des retenues de retard / départ (hr_dz_attendance_anomaly),
  pas par le moteur.

Le calcul est un rapprochement idempotent : on peut le relancer autant de fois
qu'on veut. Une absence créée pendant qu'une pointeuse était hors ligne est
remplacée par la présence réelle dès que ses pointages arrivent.

Les jours sont des jours LOCAUX (fuseau du planning, à défaut celui de
l'employé, à défaut Africa/Algiers) : un pointage à 0 h 30 heure d'Alger
appartient bien à ce jour-là, et non à la veille en UTC.
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo.exceptions import UserError
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

ENGINE_CTX = 'dz_work_entry_engine'
DEFAULT_TZ = 'Africa/Algiers'


class HrDzWorkEntryEngine(models.AbstractModel):
    _name = 'hr.dz.work.entry.engine'
    _description = 'Moteur de calcul des prestations (pointages → prestations)'

    # ── Fuseau, bornes et calendrier ─────────────────────────────────────────

    @api.model
    def _tz(self, employee, version=None):
        calendar = (version and version.resource_calendar_id) or employee.resource_calendar_id
        name = (calendar and calendar.tz) or employee.tz or DEFAULT_TZ
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(DEFAULT_TZ)

    @api.model
    def _local_date(self, dt_utc, tz):
        """Datetime naïf UTC (stockage Odoo) → date locale."""
        return pytz.utc.localize(dt_utc).astimezone(tz).date()

    @api.model
    def _utc_bounds(self, date_from, date_to, tz):
        """Jours locaux [date_from, date_to] → bornes UTC naïves [début, fin[."""
        start = tz.localize(datetime.combine(date_from, time.min))
        stop = tz.localize(datetime.combine(date_to + timedelta(days=1), time.min))
        return (start.astimezone(pytz.utc).replace(tzinfo=None),
                stop.astimezone(pytz.utc).replace(tzinfo=None))

    @api.model
    def _local_days(self, dt_from, dt_to, tz, clip_from, clip_to):
        """Jours locaux couverts par un intervalle UTC, limités à la période."""
        first = self._local_date(dt_from, tz)
        # La fin d'un intervalle est exclusive en pratique (23:59:59 ou minuit
        # du lendemain) : on recule d'une seconde pour ne pas déborder.
        last = self._local_date(dt_to - timedelta(seconds=1), tz) if dt_to > dt_from else first
        day = max(first, clip_from)
        while day <= min(last, clip_to):
            yield day
            day += timedelta(days=1)

    @api.model
    def _calendar(self, employee, version=None):
        """Planning qui s'applique : celui du contrat, sinon celui de l'employé."""
        return (version and version.resource_calendar_id) or employee.resource_calendar_id

    @api.model
    def _planned_intervals(self, employee, calendar, day):
        """Créneaux prévus au planning pour ce jour, en heure locale (naïve).

        SEULE définition du « prévu » dans biodoo : le moteur de prestations, la
        détection des retards / départs et les rapports s'en servent tous.
        - plannings sur deux semaines respectés ;
        - lignes de pause déjeuner exclues ;
        - poste de nuit : une fin à 24 h ou plus (convention Odoo) ou avant le
          début tombe le lendemain ;
        - horaire de Ramadan (hr_ramadan_schedule) s'il est actif : un seul
          créneau, éventuellement différent pour les femmes ;
        - planning à horaires flexibles : pas de créneaux fixes, donc rien
          (jamais d'absence, de retard ni de départ déduits automatiquement).
        """
        if not calendar or getattr(calendar, 'flexible_hours', False):
            return []
        lines = calendar.attendance_ids.filtered(
            lambda a: a.dayofweek == str(day.weekday()) and a.day_period != 'lunch')
        if calendar.two_weeks_calendar:
            week_type = str(self.env['resource.calendar.attendance'].get_week_type(day))
            lines = lines.filtered(lambda a: a.week_type == week_type)
        if not lines:
            return []

        midnight = datetime.combine(day, time.min)

        def interval(h_from, h_to):
            start = midnight + timedelta(hours=h_from)
            stop = midnight + timedelta(hours=h_to)
            if stop <= start:
                stop += timedelta(days=1)
            return start, stop

        if getattr(calendar, 'is_ramadan', False) and getattr(calendar, 'ramadan_hour_to', 0.0):
            r_from = calendar.ramadan_hour_from or min(lines.mapped('hour_from'))
            r_to = calendar.ramadan_hour_to
            if (getattr(calendar, 'ramadan_mode', 'uniform') == 'gender'
                    and (getattr(employee, 'sex', 'male') or 'male') == 'female'):
                r_from = calendar.ramadan_hour_from_female or r_from
                r_to = calendar.ramadan_hour_to_female or r_to
            return [interval(r_from, r_to)]

        return sorted(interval(line.hour_from, line.hour_to) for line in lines)

    @api.model
    def _planned_hours(self, employee, calendar, day):
        """Heures prévues au planning pour ce jour (0 = jour non travaillé)."""
        return sum((stop - start).total_seconds() / 3600.0
                   for start, stop in self._planned_intervals(employee, calendar, day))

    # ── Types de prestation ──────────────────────────────────────────────────

    @api.model
    def _types(self):
        Type = self.env['hr.work.entry.type']
        active = Type.search([('active', '=', True)])
        work = active.filtered('is_standard_work')[:1] or self.env.ref(
            'hr_work_entry.work_entry_type_attendance', raise_if_not_found=False)
        return {
            'work': work or Type,
            'absent': Type.search([('code', '=', 'ABSENT')], limit=1),
            'leave_fallback': Type.search([('code', '=', 'CA')], limit=1),
            'banked': Type.search([('dz_recovery_role', '=', 'banked')], limit=1),
            'overtime': {},   # taux → type, rempli à la demande (_overtime_type)
            'premium': {},    # taux → type « majoration seule » (_premium_type)
        }

    @api.model
    def _overtime_type(self, types, rate):
        """Type de prestation des heures sup. payées à `rate` (1,5 = 150 %).

        Les taux viennent des règles d'heures supplémentaires d'Odoo
        (Présences → Configuration → Règles) ; chaque taux a son type, créé au
        besoin, que la paie lit pour majorer les heures.
        """
        key = round(rate, 4)
        if key not in types['overtime']:
            Type = self.env['hr.work.entry.type']
            wtype = Type.search([('overtime_rate', '>=', key - 0.0001),
                                 ('overtime_rate', '<=', key + 0.0001),
                                 ('dz_recovery_role', '=', False)],
                                order='sequence, id', limit=1)
            if not wtype:
                pct = round((key - 1) * 100)
                wtype = Type.create({
                    'name': _('Heures sup. %s %%') % pct,
                    'code': 'OT%s' % pct,
                    'overtime_rate': key,
                    'is_leave': False,
                })
            types['overtime'][key] = wtype
        return types['overtime'][key]

    @api.model
    def _premium_type(self, types, rate):
        """Type des heures sup. dont SEULE la majoration est payée (le temps
        est rendu en repos) : la paie paie (taux − 1) × heures."""
        key = round(rate, 4)
        if key not in types['premium']:
            Type = self.env['hr.work.entry.type']
            wtype = Type.search([('overtime_rate', '>=', key - 0.0001),
                                 ('overtime_rate', '<=', key + 0.0001),
                                 ('dz_recovery_role', '=', 'premium')],
                                order='sequence, id', limit=1)
            if not wtype:
                pct = round((key - 1) * 100)
                wtype = Type.create({
                    'name': _('Majoration %s %% (temps récupéré)') % pct,
                    'code': 'MAJ%s' % pct,
                    'overtime_rate': key,
                    'dz_recovery_role': 'premium',
                    'is_leave': False,
                })
            types['premium'][key] = wtype
        return types['premium'][key]

    # ── Point d'entrée ───────────────────────────────────────────────────────

    @api.model
    def _attendance_employees(self, employees):
        """Employés gérés par le moteur : au moins une version en source Présences."""
        return employees.sudo().filtered(
            lambda e: any(v.work_entry_source == 'attendance' for v in e.version_ids))

    @api.model
    def sync(self, employees, date_from, date_to):
        """Rapproche les prestations automatiques de la réalité sur la période.

        Retourne les prestations du moteur présentes sur la période après calcul.
        """
        engine = self.sudo().with_context(**{ENGINE_CTX: True, 'tracking_disable': True})
        types = engine._types()
        result = self.env['hr.work.entry']
        for employee in engine._attendance_employees(employees):
            result |= engine._sync_employee(employee, date_from, date_to, types)
        return result

    # ── Calcul pour un employé ───────────────────────────────────────────────

    def day_facts(self, employee, date_from, date_to):
        """Ce qui était prévu et ce qui s'est passé, jour par jour.

        SEULE collecte des faits d'une journée dans biodoo : le calcul des
        prestations et les rapports d'absence / de présence la partagent, donc
        ils ne peuvent pas se contredire.

        Retourne une liste de dicts (un par jour) : day, version, in_contract,
        from_attendance, planned (heures), intervals (créneaux locaux), attendances, overtime
        ({taux: heures approuvées}), overtime_lines (lignes approuvées), holiday,
        leave, finished, absence_due, status (voir _day_status).
        """
        employee = employee.sudo()
        tz = self._tz(employee)
        today = datetime.now(tz).date()
        utc_from, utc_to = self._utc_bounds(date_from, date_to, tz)
        policy = employee._dz_attendance_policy()
        last_absence_day = today - timedelta(days=max(1, policy.absence_after_days or 1))

        att_by_day = defaultdict(lambda: self.env['hr.attendance'])
        for att in self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', utc_from),
            ('check_in', '<', utc_to),
        ], order='check_in'):
            att_by_day[self._local_date(att.check_in, tz)] |= att

        # Heures supplémentaires APPROUVÉES (règles et validation d'Odoo),
        # par jour et par taux de majoration.
        overtime = defaultdict(lambda: defaultdict(float))
        overtime_lines = defaultdict(lambda: self.env['hr.attendance.overtime.line'])
        for line in self.env['hr.attendance.overtime.line'].search([
            ('employee_id', '=', employee.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('status', '=', 'approved'),
        ], order='date, id'):
            if line.amount_rate > 1.0:
                overtime[line.date][round(line.amount_rate, 4)] += line.manual_duration
                overtime_lines[line.date] |= line

        # Jours fériés et spéciaux (globaux, avec type de prestation).
        holidays = {}
        for leave in self.env['resource.calendar.leaves'].search([
            ('resource_id', '=', False),
            ('date_from', '<', utc_to),
            ('date_to', '>', utc_from),
            ('work_entry_type_id', '!=', False),
            ('company_id', 'in', (False, employee.company_id.id)),
        ]):
            for day in self._local_days(leave.date_from, leave.date_to, tz, date_from, date_to):
                holidays.setdefault(day, leave)

        # Congés validés de l'employé.
        leaves = {}
        if 'hr.leave' in self.env:
            for leave in self.env['hr.leave'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                ('date_from', '<', utc_to),
                ('date_to', '>', utc_from),
            ]):
                for day in self._local_days(leave.date_from, leave.date_to, tz, date_from, date_to):
                    leaves.setdefault(day, leave)

        facts = []
        day = date_from
        while day <= date_to:
            version = employee._get_version(day)
            calendar = self._calendar(employee, version)
            holiday = holidays.get(day)
            if holiday and holiday.calendar_id and calendar and holiday.calendar_id != calendar:
                holiday = None   # férié propre à un autre planning
            intervals = self._planned_intervals(employee, calendar, day)
            fact = {
                'day': day,
                'version': version,
                'in_contract': bool(
                    version.contract_date_start
                    and version.contract_date_start <= day
                    and (not version.contract_date_end or version.contract_date_end >= day)),
                # Prestations calculées depuis les pointages (sinon : Odoo,
                # d'après l'horaire, pour le personnel dispensé de pointage).
                'from_attendance': version.work_entry_source == 'attendance',
                'intervals': intervals,
                'planned': sum((b - a).total_seconds() / 3600.0 for a, b in intervals),
                'attendances': att_by_day.get(day, self.env['hr.attendance']),
                'overtime': dict(overtime.get(day, {})),
                'overtime_lines': overtime_lines.get(day, self.env['hr.attendance.overtime.line']),
                'holiday': holiday,
                'leave': leaves.get(day),
                'finished': day < today,
                'absence_due': day <= last_absence_day,
            }
            fact['status'] = self._day_status(fact, policy)
            facts.append(fact)
            day += timedelta(days=1)
        return facts

    @api.model
    def _day_status(self, fact, policy):
        """Classement d'une journée, commun aux prestations et aux rapports.

        off            jour non travaillé, ou hors contrat
        holiday        férié (travaillé ou non)
        leave          congé validé, sans pointage
        present        pointé
        missing_punch  pointé mais une entrée sans sortie (journée terminée)
        absent         jour ouvré sans pointage, délai de constat écoulé
                       (ou entrée seule, si la politique la compte en absence)
        pending        jour ouvré sans pointage, pas encore constatable
        """
        if not fact['in_contract']:
            return 'off'
        planned, atts = fact['planned'], fact['attendances']
        if fact['holiday'] and (planned > 0 or atts):
            return 'holiday'
        if fact['leave'] and planned > 0 and not atts:
            return 'leave'
        if atts:
            complete = atts.filtered('check_out')
            if fact['finished'] and len(complete) < len(atts):
                if (not complete and planned > 0 and fact['absence_due']
                        and policy.missing_punch_rule == 'absent'):
                    return 'absent'
                return 'missing_punch'
            return 'present'
        if planned <= 0:
            return 'off'
        return 'absent' if fact['absence_due'] else 'pending'

    def _sync_employee(self, employee, date_from, date_to, types):
        WorkEntry = self.env['hr.work.entry'].with_context(active_test=False)
        tz = self._tz(employee)
        today = datetime.now(tz).date()
        policy = employee._dz_attendance_policy()
        cap_state = self._overtime_cap_state(employee, policy, date_from)
        if cap_state is not None:
            # Un plafond mensuel lie les jours du mois entre eux : changer un
            # jour peut déplacer le plafond pour tous les jours suivants.
            month_end = (date_to.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            date_to = max(date_to, min(month_end, today))

        existing_by_day = defaultdict(lambda: WorkEntry.browse())
        for entry in WorkEntry.search([
            ('employee_id', '=', employee.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ]):
            existing_by_day[entry.date] |= entry

        kept = WorkEntry.browse()
        recovery_days = {}
        facts = self.day_facts(employee, date_from, date_to)
        for fact in facts:
            day = fact['day']
            fact['overtime_split'] = self._overtime_split(fact, policy, cap_state)
            day_entries = existing_by_day[day]
            owned_by_hr = day_entries.filtered(
                lambda e: e.state == 'validated' or e.dz_origin == 'manual')
            # Origine vide = prestation antérieure à ce moteur (ancien assistant,
            # qui les écrasait déjà) : traitée comme automatique.
            auto = day_entries.filtered(
                lambda e: e.dz_origin in ('auto', False) and e.state != 'validated')
            if owned_by_hr:
                kept |= auto
            elif not (fact['in_contract'] and fact['from_attendance']):
                # Hors contrat ou employé passé en source Horaire : plus rien
                # d'automatique ce jour-là.
                auto.unlink()
            else:
                desired = self._desired_entries(employee, fact, types, policy)
                kept |= self._reconcile(employee, fact['version'], day, auto, desired)
                recovery_days[day] = self._recovery_of_day(employee, fact, policy)

        self.env['hr.dz.recovery']._sync_days(employee, policy, recovery_days)
        self._after_employee_sync(employee, date_from, date_to, today, facts)
        return kept

    # ── Heures sup. : payées, majoration seule ou rendues en repos ───────────

    @api.model
    def _day_kind(self, fact):
        """Nature d'une journée pour les récupérations."""
        if fact['holiday']:
            return 'holiday'
        return 'rest_day' if fact['planned'] <= 0 else 'weekday'

    def _overtime_cap_state(self, employee, policy, date_from):
        """Compteur du plafond mensuel d'heures sup. payées (None sans plafond),
        amorcé avec les heures des jours du mois qui précèdent `date_from`."""
        if not (policy.recovery_enabled and policy.recovery_overtime_mode != 'paid'
                and policy.recovery_overtime_cap > 0):
            return None
        state = {'month': (date_from.year, date_from.month), 'used': 0.0}
        month_start = date_from.replace(day=1)
        if month_start < date_from:
            for fact in self.day_facts(employee, month_start, date_from - timedelta(days=1)):
                self._overtime_split(fact, policy, state)
        return state

    def _overtime_split(self, fact, policy, cap_state=None):
        """Heures sup. approuvées du jour → [(taux, heures, traitement)].

        Traitement : 'paid' (heures sup. payées), 'mixed' (majoration payée,
        temps rendu en repos) ou 'recovered' (tout rendu en repos). SEUL
        endroit qui en décide : une heure ne peut être ni payée deux fois ni
        à la fois payée et rendue. Ordre : choix des RH sur la ligne, puis
        plafond mensuel des jours ouvrés, puis politique.
        """
        kind = self._day_kind(fact)
        split = []
        for line in fact['overtime_lines']:
            rate, hours = round(line.amount_rate, 4), line.manual_duration
            if hours <= 0:
                continue
            if policy.recovery_enabled and line.dz_compensation:
                split.append((rate, hours, line.dz_compensation))
                continue
            treatment = policy._recovery_mode(kind)
            if kind == 'weekday' and cap_state is not None:
                month = (fact['day'].year, fact['day'].month)
                if cap_state['month'] != month:
                    cap_state.update(month=month, used=0.0)
                paid = max(0.0, min(hours, policy.recovery_overtime_cap - cap_state['used']))
                cap_state['used'] += hours
                if paid > 0:
                    split.append((rate, paid, 'paid'))
                hours -= paid
                if hours <= 0:
                    continue
            split.append((rate, hours, treatment))
        return split

    def _recovery_of_day(self, employee, fact, policy):
        """Droit à récupération ouvert par la journée (heures 0 = aucun)."""
        banked = [item for item in fact.get('overtime_split', []) if item[2] != 'paid']
        kind = self._day_kind(fact)
        return {
            'kind': kind,
            'banked': banked,
            'worked': sum(hours for _r, hours, _t in banked),
            'hours': policy._recovery_credit(employee, fact['day'], kind, banked) if banked else 0.0,
            'version': fact['version'],
        }

    def _desired_entries(self, employee, fact, types, policy):
        """Prestations attendues pour une journée : [(type, durée, libellé)]."""
        status, planned = fact['status'], fact['planned']
        label_day = fact['day'].strftime('%d/%m/%Y')
        desired = []

        complete = fact['attendances'].filtered('check_out')
        overtime = fact['overtime']
        normal = sum(complete.mapped('worked_hours')) - sum(overtime.values())
        if status == 'missing_punch' and (
                policy.missing_punch_rule == 'planned'
                or (policy.missing_punch_rule == 'hr'
                    and not self._missing_punch_decided_by_hr(employee, policy))):
            # Sortie oubliée : l'employé est présumé avoir fait sa journée
            # (« à trancher par les RH » sans file de décision : idem).
            normal = max(normal, planned)
        if normal > 0 and types['work']:
            desired.append((types['work'], normal, _('Présence %s') % label_day))
        split = fact.get('overtime_split')
        if split is None:
            split = [(rate, hours, 'paid') for rate, hours in overtime.items()]
        by_type = defaultdict(float)
        for rate, hours, treatment in split:
            if treatment == 'mixed':
                wtype = self._premium_type(types, rate)
            elif treatment == 'recovered' and types['banked']:
                wtype = types['banked']
            else:
                wtype = self._overtime_type(types, rate)
            by_type[wtype] += hours
        for wtype, hours in by_type.items():
            if hours > 0:
                label = (_('Heures mises en récupération %s') % label_day
                         if wtype.dz_recovery_role == 'banked'
                         else _('Heures sup. %s') % label_day)
                desired.append((wtype, hours, label))

        if status == 'holiday':
            holiday = fact['holiday']
            desired.append((holiday.work_entry_type_id, planned, '%s %s' % (holiday.name, label_day)))
        elif status == 'leave':
            leave = fact['leave']
            wtype = leave.holiday_status_id.work_entry_type_id or types['leave_fallback']
            if wtype:
                desired.append((wtype, planned, '%s %s' % (leave.holiday_status_id.name, label_day)))
            else:
                _logger.warning("Prestations — congé « %s » de %s sans type de prestation, ignoré",
                                leave.holiday_status_id.name, employee.name)
        elif (status == 'absent' and types['absent']
              and not self._absence_decided_by_hr(employee, policy)):
            desired.append((types['absent'], planned, _('Absence %s') % label_day))
        return desired

    def _absence_decided_by_hr(self, employee, policy):
        """Point d'extension : vrai si une absence attend la décision des RH au
        lieu de devenir directement une prestation ABSENT (mode manuel, géré
        par hr_dz_attendance_anomaly)."""
        return False

    def _missing_punch_decided_by_hr(self, employee, policy):
        """Point d'extension : vrai si les heures d'une sortie manquante
        attendent la décision des RH (géré par hr_dz_attendance_anomaly)."""
        return False

    def _reconcile(self, employee, version, day, auto, desired):
        """Aligne les prestations automatiques du jour sur la liste attendue."""
        WorkEntry = self.env['hr.work.entry']
        by_type = defaultdict(lambda: WorkEntry.browse())
        for entry in auto:
            by_type[entry.work_entry_type_id.id] |= entry

        kept = WorkEntry.browse()
        for wtype, duration, name in desired:
            current = by_type.pop(wtype.id, WorkEntry.browse())
            entry, duplicates = current[:1], current[1:]
            duplicates.unlink()
            if entry:
                vals = {}
                if abs((entry.duration or 0.0) - duration) > 1e-6:
                    vals['duration'] = duration
                if not entry.active:
                    vals['active'] = True
                if entry.version_id != version:
                    vals['version_id'] = version.id
                if vals:
                    entry.write(vals)
            else:
                entry = WorkEntry.create({
                    'name': name,
                    'date': day,
                    'duration': duration,
                    'work_entry_type_id': wtype.id,
                    'employee_id': employee.id,
                    'version_id': version.id,
                    'company_id': employee.company_id.id,
                    'dz_origin': 'auto',
                })
            kept |= entry

        # Ce qui n'est plus attendu (ex. absence remplacée par la présence réelle).
        for leftovers in by_type.values():
            leftovers.unlink()
        return kept

    def _after_employee_sync(self, employee, date_from, date_to, today, facts):
        """Point d'extension : appelé après le calcul d'un employé, avec les
        faits des journées (les décisions RH y appliquent la politique)."""
        return

    # ── Déclencheurs ─────────────────────────────────────────────────────────

    @api.model
    def sync_attendance_days(self, attendances):
        """Recalcule les jours touchés par des pointages créés / modifiés / supprimés.

        Appelé à chaque arrivée de pointages (synchronisation d'une pointeuse
        revenue en ligne comprise). Ne doit jamais faire échouer l'enregistrement
        du pointage : une erreur est journalisée, le calcul de nuit rattrapera.
        """
        days_by_employee = defaultdict(set)
        for att in attendances.sudo():
            if att.employee_id and att.check_in:
                tz = self._tz(att.employee_id)
                days_by_employee[att.employee_id].add(self._local_date(att.check_in, tz))
        self.sync_days(days_by_employee)

    @api.model
    def sync_days(self, days_by_employee):
        """{employé: {jours}} → recalcule chaque employé sur l'étendue de ses jours."""
        for employee, days in days_by_employee.items():
            if not days:
                continue
            self._sync_guarded(employee, min(days), max(days))

    @api.model
    def _cron_sync_recent(self, days_back=None):
        """Calcul de nuit : du 1er du mois précédent jusqu'à hier, tous les employés.

        Le mois précédent est inclus parce que des pointages peuvent arriver en
        retard (pointeuse restée hors ligne) avant la clôture de la paie ; les
        jours déjà validés ne sont de toute façon jamais modifiés.
        """
        today = fields.Date.context_today(self)
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        if days_back:
            start = today - timedelta(days=days_back)
        employees = self.env['hr.employee'].sudo().search([])
        for index, employee in enumerate(self._attendance_employees(employees), 1):
            self._sync_guarded(employee, start, today)
            # Par lots : un calcul de nuit sur des centaines d'employés ne tient
            # pas une seule transaction (verrous, perte totale au moindre arrêt).
            if index % 50 == 0 and not getattr(threading.current_thread(), 'testing', False):
                self.env.cr.commit()  # biodoo: commit-ok tâche planifiée par lots

    @api.model
    def _sync_guarded(self, employee, date_from, date_to):
        """Recalcule un employé dans un savepoint ; l'erreur reste visible.

        Une erreur n'arrête pas les autres employés, et elle est inscrite sur
        la fiche de l'employé (« Erreur de calcul des prestations ») et
        comptée au tableau de bord, au lieu de rester dans le journal.
        """
        emp = employee.sudo()
        try:
            with self.env.cr.savepoint():
                self.sync(employee, date_from, date_to)
        except Exception as exc:  # biodoo: except-ok erreur inscrite sur l'employé et journalisée
            _logger.exception("Prestations — recalcul impossible pour %s (%s → %s)",
                              employee.name, date_from, date_to)
            message = exc.args[0] if isinstance(exc, UserError) else (
                "Erreur technique (%s) — voir le journal du serveur." % type(exc).__name__)
            emp.write({'dz_work_entry_error': '%s → %s : %s' % (date_from, date_to, message),
                       'dz_work_entry_error_date': fields.Datetime.now()})
            return False
        if emp.dz_work_entry_error:
            emp.write({'dz_work_entry_error': False, 'dz_work_entry_error_date': False})
        return True
