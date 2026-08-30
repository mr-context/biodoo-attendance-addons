"""
Wizard pour calculer les prestations depuis les présences.
Détecte dynamiquement les types de prestations depuis:
- Les présences (hr.attendance) → type par défaut (Attendance)
- Les heures supplémentaires validées → type overtime
- Les jours spéciaux (resource.calendar.leaves) → type configuré sur le leave
- Les congés approuvés (hr.leave) → type configuré sur le hr.leave.type
"""

import logging
from datetime import datetime, time, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrWorkEntryComputeWizard(models.TransientModel):
    _name = 'hr.work.entry.compute.wizard'
    _description = 'Calculer les prestations'

    date_from = fields.Date(
        string='Date début',
        required=True,
        default=lambda self: fields.Date.today().replace(day=1),
    )
    date_to = fields.Date(
        string='Date fin',
        required=True,
        default=fields.Date.today,
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        string='Employés',
        help='Laisser vide pour tous les employés avec source "Présences"',
    )
    force_regenerate = fields.Boolean(
        string='Régénérer existants',
        default=False,
        help='Supprimer et recréer les prestations existantes',
    )

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_employee_working_days(self, employee):
        """Retourne le set des jours ouvrables (0=lundi..6=dimanche) depuis le calendrier."""
        calendar = employee.resource_id.calendar_id
        if not calendar:
            # Fallback: lundi-jeudi + dimanche (standard Algérie)
            return {0, 1, 2, 3, 6}
        return set(int(att.dayofweek) for att in calendar.attendance_ids)

    def _get_calendar_day_hours(self, employee, work_date):
        """Retourne les heures théoriques du calendrier pour un jour donné.

        En mode Ramadan (hr_ramadan_schedule), les heures sont lues depuis les
        champs dédiés du calendrier via getattr (sans modifier les lignes).
        """
        calendar = employee.resource_id.calendar_id
        if not calendar:
            return 8.0  # fallback

        # ── Override Ramadan ──────────────────────────────────────────────
        if getattr(calendar, 'is_ramadan', False):
            r_from = getattr(calendar, 'ramadan_hour_from', 0.0)
            r_to = getattr(calendar, 'ramadan_hour_to', 0.0)
            if r_to:
                if getattr(calendar, 'ramadan_mode', 'uniform') == 'gender':
                    sex = getattr(employee, 'sex', 'male') or 'male'
                    if sex == 'female':
                        f_from = getattr(calendar, 'ramadan_hour_from_female', 0.0)
                        f_to = getattr(calendar, 'ramadan_hour_to_female', 0.0)
                        effective_to = f_to if f_to else r_to
                        effective_from = f_from if f_from else r_from
                    else:
                        effective_to = r_to
                        effective_from = r_from
                else:
                    effective_to = r_to
                    effective_from = r_from
                return max(0.0, effective_to - effective_from)

        # ── Heures normales du calendrier ─────────────────────────────────
        day_of_week = str(work_date.weekday())
        day_lines = calendar.attendance_ids.filtered(
            lambda a: a.dayofweek == day_of_week)
        return sum(line.hour_to - line.hour_from for line in day_lines)

    def _get_calendar_leaves_in_range(self):
        """Récupère les jours spéciaux (fériés, etc.) avec un type de prestation."""
        return self.env['resource.calendar.leaves'].search([
            ('resource_id', '=', False),  # Global uniquement
            ('date_from', '<=', datetime.combine(self.date_to, time.max)),
            ('date_to', '>=', datetime.combine(self.date_from, time.min)),
            ('work_entry_type_id', '!=', False),
        ])

    def _get_leave_dates(self, leave):
        """Retourne la liste des dates couvertes par un leave dans la plage du wizard."""
        start = max(leave.date_from.date(), self.date_from)
        end = min(leave.date_to.date(), self.date_to)
        dates = []
        current = start
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
        return dates

    def _leave_applies_to_employee(self, leave, employee):
        """Vérifie si un leave global s'applique au calendrier de l'employé."""
        if not leave.calendar_id:
            return True  # Pas de calendrier spécifique = tous
        employee_calendar = employee.resource_id.calendar_id
        if not employee_calendar:
            return True  # Pas de calendrier employé = on applique
        return leave.calendar_id.id == employee_calendar.id

    # ── Action principale ─────────────────────────────────────────────

    def action_compute(self):
        """Calculer les prestations depuis les présences + jours spéciaux + congés."""
        self.ensure_one()

        _logger.info("=" * 70)
        _logger.info("DZ WE WIZARD — début calcul %s → %s  force=%s",
                     self.date_from, self.date_to, self.force_regenerate)

        # Récupérer les employés
        if self.employee_ids:
            employees = self.employee_ids
        else:
            employees = self.env['hr.employee'].search([])

        if not employees:
            raise UserError(_('Aucun employé trouvé.'))

        _logger.info("DZ WE WIZARD — %d employé(s) à traiter", len(employees))

        WorkEntry = self.env['hr.work.entry']
        Attendance = self.env['hr.attendance']

        # ── Types de prestations ─────────────────────────────────────────
        # is_standard_work est NON-STOCKÉ (compute) → filtre Python obligatoire.
        WorkEntryType = self.env['hr.work.entry.type']
        all_active_types = WorkEntryType.search([('active', '=', True)])

        work_type  = all_active_types.filtered(lambda t: t.is_standard_work)[:1]
        late_type  = WorkEntryType.search([('is_late_deduction',  '=', True)], limit=1)
        early_type = WorkEntryType.search([('is_early_deduction', '=', True)], limit=1)

        # Types heures supp (triés par taux croissant pour choix cohérent)
        overtime_types = WorkEntryType.search(
            [('overtime_rate', '>', 1.0)], order='overtime_rate asc')
        overtime_type = overtime_types[:1]

        _logger.info(
            "DZ WE WIZARD — types détectés :\n"
            "  work_type    = %s (id=%s, is_standard_work=%s)\n"
            "  late_type    = %s (id=%s)\n"
            "  early_type   = %s (id=%s)\n"
            "  overtime_type= %s (id=%s)\n"
            "  all overtime = %s",
            work_type.code if work_type else 'AUCUN', work_type.id if work_type else '-',
            work_type.is_standard_work if work_type else '-',
            late_type.code if late_type else 'AUCUN', late_type.id if late_type else '-',
            early_type.code if early_type else 'AUCUN', early_type.id if early_type else '-',
            overtime_type.code if overtime_type else 'AUCUN', overtime_type.id if overtime_type else '-',
            [(t.code, t.overtime_rate) for t in overtime_types],
        )

        # Diagnostic is_standard_work pour tous les types actifs
        _logger.info("DZ WE WIZARD — diagnostic is_standard_work / is_paid par type :")
        for t in all_active_types:
            _logger.info(
                "  [%s] is_deductible=%s overtime_rate=%s is_leave=%s"
                " → is_paid=%s is_standard_work=%s is_late=%s is_early=%s",
                t.code, t.is_deductible, t.overtime_rate, t.is_leave,
                t.is_paid, t.is_standard_work,
                t.is_late_deduction, t.is_early_deduction,
            )

        if not work_type:
            # Fallback ultime si le flag n'est pas configuré
            work_type = self.env.ref(
                'hr_work_entry.work_entry_type_attendance',
                raise_if_not_found=False)
            _logger.warning(
                "DZ WE WIZARD — aucun type is_standard_work=True trouvé ! "
                "Fallback sur work_entry_type_attendance : %s",
                work_type.code if work_type else 'introuvable')

        # Jours spéciaux dans la plage (fériés, ramadan, etc.)
        calendar_leaves = self._get_calendar_leaves_in_range()
        _logger.info("DZ WE WIZARD — %d jour(s) spécial(aux) dans la plage",
                     len(calendar_leaves))

        # Congés approuvés dans la plage (hr.leave validés)
        hr_leaves = self.env['hr.leave'].search([
            ('date_from', '<=', datetime.combine(self.date_to, time.max)),
            ('date_to',   '>=', datetime.combine(self.date_from, time.min)),
            ('state', '=', 'validate'),
        ]) if 'hr.leave' in self.env else self.env['hr.leave'].browse()
        _logger.info("DZ WE WIZARD — %d congé(s) hr.leave validé(s) dans la plage",
                     len(hr_leaves))

        work_entries = WorkEntry

        for employee in employees:
            _logger.info("-" * 60)
            _logger.info("DZ WE WIZARD — employé : %s", employee.name)

            # ── Suppression des anciennes prestations si demandé ──────────
            # Stratégie : collecter les IDs des work entries liés à des déductions
            # validées → les exclure de la suppression (indépendant des flags).
            if self.force_regenerate:
                protected_ids = []
                if 'hr.attendance.deduction' in self.env:
                    protected_ids = self.env['hr.attendance.deduction'].search([
                        ('employee_id', '=', employee.id),
                        ('date', '>=', self.date_from),
                        ('date', '<=', self.date_to),
                        ('status', '=', 'validated'),
                        ('work_entry_id', '!=', False),
                    ]).mapped('work_entry_id').ids

                del_domain = [
                    ('employee_id', '=', employee.id),
                    ('date', '>=', self.date_from),
                    ('date', '<=', self.date_to),
                ]
                if protected_ids:
                    del_domain.append(('id', 'not in', protected_ids))

                old_entries = WorkEntry.search(del_domain)
                _logger.info(
                    "DZ WE WIZARD — %s : suppression de %d prestation(s) "
                    "(%d work entries LATE/EARLY protégés : %s)",
                    employee.name, len(old_entries), len(protected_ids), protected_ids)
                old_entries.unlink()

            # Calendrier de travail de l'employé (calculé une seule fois)
            working_days = self._get_employee_working_days(employee)
            _logger.info("DZ WE WIZARD — %s : jours ouvrables = %s",
                         employee.name, sorted(working_days))

            # ── 1. Présences ──────────────────────────────────────────────
            attendances = Attendance.search([
                ('employee_id', '=', employee.id),
                ('check_in', '>=', datetime.combine(self.date_from, time.min)),
                ('check_in', '<=', datetime.combine(self.date_to, time.max)),
                ('check_out', '!=', False),
            ])
            _logger.info("DZ WE WIZARD — %s : section 1 — %d présence(s) trouvée(s)",
                         employee.name, len(attendances))

            # Grouper par jour
            by_date = {}
            for att in attendances:
                work_date = att.check_in.date()
                if work_date not in by_date:
                    by_date[work_date] = {'normal': 0, 'overtime': 0}

                ot_hours = att.validated_overtime_hours or 0
                normal_hours = att.worked_hours - ot_hours

                by_date[work_date]['normal'] += normal_hours
                by_date[work_date]['overtime'] += ot_hours

            _logger.info("DZ WE WIZARD — %s : %d jour(s) avec présence",
                         employee.name, len(by_date))

            # Créer (ou mettre à jour) les prestations de présence.
            # IDEMPOTENT : le pipeline auto tourne à CHAQUE check_out, donc on
            # ne doit jamais dupliquer. On upsert par (employé, date, type) :
            # entry existante → on ajuste la durée ; sinon → create.
            # Pas de delete+recreate → pas de churn d'ID.
            def _upsert_we(work_date, wtype, duration, label):
                existing = WorkEntry.search([
                    ('employee_id', '=', employee.id),
                    ('date', '=', work_date),
                    ('work_entry_type_id', '=', wtype.id),
                ], limit=1)
                if existing:
                    if abs((existing.duration or 0.0) - duration) > 1e-6:
                        existing.duration = duration
                    return existing, False
                return WorkEntry.create({
                    'name': label % work_date.strftime('%d/%m/%Y'),
                    'date': work_date,
                    'duration': duration,
                    'work_entry_type_id': wtype.id,
                    'employee_id': employee.id,
                    'company_id': employee.company_id.id,
                }), True

            created_work = 0
            created_ot = 0
            for work_date, hours in by_date.items():
                if hours['normal'] > 0 and work_type:
                    entry, is_new = _upsert_we(
                        work_date, work_type, hours['normal'], _('Présence %s'))
                    work_entries |= entry
                    created_work += int(is_new)

                if hours['overtime'] > 0 and overtime_type:
                    entry, is_new = _upsert_we(
                        work_date, overtime_type, hours['overtime'], _('Heures sup. %s'))
                    work_entries |= entry
                    created_ot += int(is_new)

            _logger.info(
                "DZ WE WIZARD — %s : section 1 — créé %d WORK + %d OT (upsert idempotent)",
                employee.name, created_work, created_ot)

            # ── 2. Jours spéciaux (fériés, etc.) ──────────────────────────
            has_any_attendance = bool(by_date)

            for leave in calendar_leaves:
                if not self._leave_applies_to_employee(leave, employee):
                    continue

                for leave_date in self._get_leave_dates(leave):
                    is_working_day = leave_date.weekday() in working_days
                    has_attendance = leave_date in by_date

                    if not is_working_day and not has_attendance:
                        continue
                    if not has_attendance and not has_any_attendance:
                        continue

                    if not self.force_regenerate:
                        existing = WorkEntry.search([
                            ('employee_id', '=', employee.id),
                            ('date', '=', leave_date),
                            ('work_entry_type_id', '=', leave.work_entry_type_id.id),
                        ], limit=1)
                        if existing:
                            continue

                    duration = self._get_calendar_day_hours(employee, leave_date)
                    entry = WorkEntry.create({
                        'name': '%s %s' % (
                            leave.name, leave_date.strftime('%d/%m/%Y')),
                        'date': leave_date,
                        'duration': duration,
                        'work_entry_type_id': leave.work_entry_type_id.id,
                        'employee_id': employee.id,
                        'company_id': employee.company_id.id,
                    })
                    work_entries |= entry

            # ── 3. Déductions validées (retard / départ anticipé) ──────────
            # Lit directement depuis hr.attendance.deduction (modèle biotime_connector).
            # action_validate() crée les work entries par CODE ('LATE'/'EARLY').
            # Si work_entry_id a été supprimé (force_regenerate), on le recrée.
            _logger.info(
                "DZ WE WIZARD — %s : section 3 — hr.attendance.deduction présent=%s",
                employee.name, 'hr.attendance.deduction' in self.env)

            if 'hr.attendance.deduction' in self.env:
                deductions = self.env['hr.attendance.deduction'].search([
                    ('employee_id', '=', employee.id),
                    ('date',        '>=', self.date_from),
                    ('date',        '<=', self.date_to),
                    ('status',      '=',  'validated'),
                ])
                _logger.info(
                    "DZ WE WIZARD — %s : %d déduction(s) validée(s) trouvée(s)",
                    employee.name, len(deductions))

                for ded in deductions:
                    _logger.info(
                        "DZ WE WIZARD — %s : ded id=%s type=%s date=%s "
                        "duration=%.4f work_entry_id=%s",
                        employee.name, ded.id, ded.deduction_type, ded.date,
                        ded.duration,
                        ded.work_entry_id.id if ded.work_entry_id else None,
                    )
                    if ded.work_entry_id and ded.work_entry_id.exists():
                        work_entries |= ded.work_entry_id
                    else:
                        # work_entry supprimé (ondelete='set null') → recréer par code
                        code  = 'LATE' if ded.deduction_type == 'late' else 'EARLY'
                        wtype = WorkEntryType.search([('code', '=', code)], limit=1)
                        # Fallback sur les flags si code introuvable
                        if not wtype:
                            wtype = late_type if ded.deduction_type == 'late' else early_type
                        if wtype:
                            try:
                                entry = WorkEntry.create({
                                    'employee_id':        employee.id,
                                    'date':               ded.date,
                                    'duration':           ded.duration,
                                    'work_entry_type_id': wtype.id,
                                })
                                ded.work_entry_id = entry
                                work_entries |= entry
                                _logger.info(
                                    "DZ WE WIZARD — %s : work entry %s recréé "
                                    "pour déduction %s (%s)",
                                    employee.name, entry.id, ded.id, code)
                            except Exception as e:
                                _logger.warning(
                                    "DZ WE WIZARD — recréation work entry "
                                    "déduction %s impossible : %s", ded.id, e)
                        else:
                            _logger.warning(
                                "DZ WE WIZARD — type '%s' introuvable pour "
                                "déduction %s, ignorée.", code, ded.id)

            # ── 4. Congés approuvés (hr.leave) ────────────────────────────
            emp_leaves = hr_leaves.filtered(lambda l: l.employee_id == employee)
            _logger.info(
                "DZ WE WIZARD — %s : section 4 — %d congé(s) hr.leave",
                employee.name, len(emp_leaves))

            for leave in emp_leaves:
                wtype = leave.holiday_status_id.work_entry_type_id

                if not wtype:
                    wtype = self.env['hr.work.entry.type'].search(
                        [('code', '=', 'CA')], limit=1)
                    if wtype:
                        _logger.warning(
                            "DZ WE: type de congé '%s' sans work_entry_type_id — "
                            "fallback sur code CA. Configurez le champ dans "
                            "Congés → Types → %s → Type de prestation.",
                            leave.holiday_status_id.name,
                            leave.holiday_status_id.name)
                    else:
                        _logger.warning(
                            "DZ WE: congé '%s' de %s ignoré — "
                            "aucun type de prestation (code CA introuvable).",
                            leave.holiday_status_id.name, employee.name)
                        continue

                leave_start = max(
                    (leave.date_from + timedelta(hours=12)).date(), self.date_from)
                leave_end = min(leave.date_to.date(), self.date_to)

                _logger.info(
                    "DZ WE: %s — congé %s du %s au %s (wtype=%s)",
                    employee.name, leave.holiday_status_id.name,
                    leave_start, leave_end, wtype.code)
                current = leave_start

                while current <= leave_end:
                    if current.weekday() not in working_days:
                        current += timedelta(days=1)
                        continue

                    if current in by_date:
                        _logger.info(
                            "DZ WE: %s %s — présence ce jour, pas de prestation congé",
                            employee.name, current)
                        current += timedelta(days=1)
                        continue

                    if not self.force_regenerate:
                        existing = WorkEntry.search([
                            ('employee_id', '=', employee.id),
                            ('date', '=', current),
                            ('work_entry_type_id', '=', wtype.id),
                        ], limit=1)
                        if existing:
                            current += timedelta(days=1)
                            continue

                    duration = self._get_calendar_day_hours(employee, current)
                    entry = WorkEntry.create({
                        'name': '%s %s' % (
                            leave.holiday_status_id.name,
                            current.strftime('%d/%m/%Y')),
                        'date': current,
                        'duration': duration,
                        'work_entry_type_id': wtype.id,
                        'employee_id': employee.id,
                        'company_id': employee.company_id.id,
                    })
                    work_entries |= entry
                    current += timedelta(days=1)

        _logger.info("=" * 70)
        _logger.info("DZ WE WIZARD — terminé : %d prestation(s) au total",
                     len(work_entries))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Prestations générées (%d)') % len(work_entries),
            'res_model': 'hr.work.entry',
            'view_mode': 'list,form',
            'domain': [('id', 'in', work_entries.ids)] if work_entries else [],
            'target': 'current',
        }
