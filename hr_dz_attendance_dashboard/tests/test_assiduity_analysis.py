"""
Analyse d'assiduité : agrégats, score, alertes, comparaison, corrections.

Octobre 2026, horaire dimanche-jeudi 8 h-12 h / 13 h-17 h à Alger (UTC+1) ;
le 4 octobre est un dimanche. Période analysée : du 4 au 15 octobre, soit
10 jours ouvrés.
"""

from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dz_assiduity')
class TestAssiduityAnalysis(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        lines = []
        for day in ('6', '0', '1', '2', '3'):
            lines += [
                (0, 0, {'name': 'Matin', 'dayofweek': day, 'hour_from': 8, 'hour_to': 12,
                        'day_period': 'morning'}),
                (0, 0, {'name': 'Après-midi', 'dayofweek': day, 'hour_from': 13, 'hour_to': 17,
                        'day_period': 'afternoon'}),
            ]
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'Dim-Jeu Alger (assiduité)', 'tz': 'Africa/Algiers', 'attendance_ids': lines,
        })
        cls.department = cls.env['hr.department'].create({'name': 'Atelier assiduité'})
        cls.policy = cls.env['hr.attendance.policy'].create({
            'name': 'Politique assiduité', 'mode': 'manual',
            'late_tolerance_minutes': 5, 'early_tolerance_minutes': 5,
        })
        cls.employee = cls._make_employee('Employé suivi')
        cls.colleague = cls._make_employee('Collègue ponctuel')
        cls.analysis = cls.env['hr.dz.assiduity.analysis']
        company = cls.env.company
        company.write({
            'dz_score_late_penalty': 1, 'dz_score_early_penalty': 1,
            'dz_score_absence_penalty': 5, 'dz_score_missing_penalty': 2,
            'dz_score_good_threshold': 90, 'dz_score_warning_threshold': 75,
            'dz_alert_late_count': 3, 'dz_alert_tolerance_count': 1,
        })

    @classmethod
    def _make_employee(cls, name):
        employee = cls.env['hr.employee'].create({
            'name': name, 'resource_calendar_id': cls.calendar.id,
            'tz': 'Africa/Algiers', 'department_id': cls.department.id,
        })
        employee.version_id.write({
            'contract_date_start': date(2026, 1, 1),
            'resource_calendar_id': cls.calendar.id,
            'work_entry_source': 'attendance',
        })
        employee.attendance_policy_id = cls.policy
        return employee

    def _day(self, employee, day, late_min=0, early_min=0):
        """Une journée pointée en deux segments, heures d'Alger → UTC naïf."""
        base = datetime(day.year, day.month, day.day) - timedelta(hours=1)
        return self.env['hr.attendance'].create([
            {'employee_id': employee.id,
             'check_in': base + timedelta(hours=8, minutes=late_min),
             'check_out': base + timedelta(hours=12)},
            {'employee_id': employee.id,
             'check_in': base + timedelta(hours=13),
             'check_out': base + timedelta(hours=17, minutes=-early_min)},
        ])

    def _scenario(self):
        late = {4: 20, 6: 10, 7: 3, 12: 15}
        for d in (4, 5, 6, 7, 11, 12, 13, 14, 15):     # le 8 (jeudi) : absent
            self._day(self.employee, date(2026, 10, d),
                      late_min=late.get(d, 0), early_min=30 if d == 11 else 0)
        for d in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15):
            self._day(self.colleague, date(2026, 10, d))

    def _analyse(self):
        return self.analysis.analyse(self.employee, date(2026, 10, 4), date(2026, 10, 15))

    @freeze_time('2026-10-20 12:00:00')
    def test_summary_reads_engine_and_anomalies(self):
        self._scenario()
        s = self._analyse()['summary']
        self.assertEqual(s['expected_days'], 10)
        self.assertEqual(s['present_days'], 9)
        self.assertEqual(s['presence_rate'], 90.0)
        self.assertEqual(s['absent_count'], 1)
        # 3 min de retard : dans la tolérance, ni retard ni pénalité.
        self.assertEqual(s['late_count'], 3)
        self.assertEqual(s['late_minutes'], 45)
        self.assertEqual(s['late_max'], 20)
        self.assertEqual(s['tolerance_edge_count'], 1)
        self.assertEqual(s['early_count'], 1)
        self.assertEqual(s['early_minutes'], 30)
        self.assertEqual(s['planned_hours'], 80.0)
        # 100 − 3 retards − 1 départ − 5 (absence) = 91.
        self.assertEqual(s['score'], 91)
        self.assertEqual(s['level'], 'good')

    @freeze_time('2026-10-20 12:00:00')
    def test_alerts(self):
        self._scenario()
        titles = [a['title'] for a in self._analyse()['alerts']]
        self.assertIn('1 absence(s) injustifiée(s)', titles)
        self.assertIn('3 retards en 30 jours (seuil 3)', titles)
        # Absent le jeudi 8, le vendredi 9 est un repos : « pont ».
        self.assertIn('1 absence(s) collée(s) à un repos, férié ou congé', titles)
        self.assertIn('1 arrivée(s) juste dans la tolérance', titles)
        levels = [a['level'] for a in self._analyse()['alerts']]
        self.assertEqual(levels, sorted(levels, key=['danger', 'warning', 'info'].index))

    @freeze_time('2026-10-20 12:00:00')
    def test_comparison_with_department(self):
        self._scenario()
        c = self._analyse()['comparison']
        self.assertEqual(c['scope'], 'Atelier assiduité')
        self.assertEqual(c['peer_count'], 2)
        self.assertEqual(c['employee']['late_count'], 3)
        self.assertEqual(c['average']['late_count'], 1.5)
        self.assertEqual(c['late_rank'], 1)

    @freeze_time('2026-10-20 12:00:00')
    def test_calendar_journal_and_trend(self):
        self._scenario()
        result = self._analyse()
        self.assertEqual(len(result['calendar']), 12)
        status = {c['day']: c['status'] for c in result['calendar']}
        self.assertEqual(status['2026-10-08'], 'absent')
        self.assertEqual(status['2026-10-09'], 'off')
        self.assertEqual(len(result['trend']), 12)
        self.assertEqual(result['trend'][-1]['month'], '2026-10-01')
        self.assertEqual(result['journal'][0]['day'], '2026-10-15', 'journal du plus récent au plus ancien')
        self.assertEqual(len(result['drill']['late_ids']), 3)
        self.assertEqual(result['drill']['absent_days'], ['2026-10-08'])

    @freeze_time('2026-10-20 12:00:00')
    def test_manual_correction_is_reported(self):
        atts = self._day(self.employee, date(2026, 10, 5))
        # Création par la pointeuse (OdooBot) : sa propre transaction, comme en vrai.
        self.env.flush_all()
        self.env.cr.precommit.run()
        officer = self.env['res.users'].create({
            'name': 'RH Correctrice', 'login': 'rh_assiduite',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                 self.env.ref('hr.group_hr_user').id,
                                 self.env.ref('hr_attendance.group_hr_attendance_manager').id])],
        })
        atts[0].with_user(officer).write({'check_in': atts[0].check_in + timedelta(minutes=-10)})
        self.env.flush_all()
        self.env.cr.precommit.run()
        corrections = self._analyse()['corrections']
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]['author'], 'RH Correctrice')
        self.assertEqual(corrections[0]['what'], 'Entrée')

    @freeze_time('2026-10-20 12:00:00')
    def test_long_period_score_is_normalised(self):
        """Au-delà d'un mois de jours ouvrés, les pénalités sont ramenées à un mois."""
        s = self.analysis._score(
            {'expected_days': 44, 'late_count': 4, 'early_count': 0,
             'absent_count': 2, 'missing_count': 0},
            self.analysis._score_config(self.env.company))
        self.assertEqual(s['score'], 93)     # (4 + 10) × 22 / 44 = 7

    @freeze_time('2026-10-20 12:00:00')
    def test_smart_button_and_report(self):
        self._scenario()
        self.assertIsInstance(self.employee.dz_assiduity_score, int)
        self.assertIn(self.employee.dz_assiduity_level, ('good', 'warning', 'danger', 'none'))
        html, _type = self.env['ir.actions.report']._render_qweb_html(
            'hr_dz_attendance_dashboard.report_employee_assiduity', self.employee.ids,
            data={'date_from': '2026-10-04', 'date_to': '2026-10-15'})
        self.assertIn(b'Employ\xc3\xa9 suivi', html)
        # Bouton « Relevé PDF » : la période voyage dans data, et l'URL du
        # rapport ne porte alors plus les ids → ils doivent être dans data.
        action = self.employee.with_context(discard_logo_check=True).action_print_assiduity(
            '2026-10-04', '2026-10-15')
        self.assertEqual(action['data']['employee_ids'], self.employee.ids)
        html, _type = self.env['ir.actions.report']._render_qweb_html(
            'hr_dz_attendance_dashboard.report_employee_assiduity', None, data=action['data'])
        self.assertIn(b'Employ\xc3\xa9 suivi', html)
        self.assertIn(b'3 retards en 30 jours', html)

    # ── Pause déjeuner ───────────────────────────────────────────────────────

    def _segments(self, day, out_h, out_m, in_h, in_m):
        base = datetime(day.year, day.month, day.day) - timedelta(hours=1)
        self.env['hr.attendance'].create([
            {'employee_id': self.employee.id, 'check_in': base + timedelta(hours=8),
             'check_out': base + timedelta(hours=out_h, minutes=out_m)},
            {'employee_id': self.employee.id, 'check_in': base + timedelta(hours=in_h, minutes=in_m),
             'check_out': base + timedelta(hours=17)},
        ])

    @freeze_time('2026-10-20 12:00:00')
    def test_lunch_break(self):
        self.calendar.zkteco_break_tolerance = 5 if 'zkteco_break_tolerance' in self.calendar._fields else 0
        self._segments(date(2026, 10, 4), 12, 30, 14, 0)    # 12:30 → 14:00 : 1 h 30 au lieu d'1 h
        self._segments(date(2026, 10, 5), 12, 30, 13, 30)   # décalée, durée respectée
        self._segments(date(2026, 10, 6), 12, 0, 13, 2)     # dans la tolérance
        result = self.analysis.analyse(self.employee, date(2026, 10, 4), date(2026, 10, 6))
        s = result['summary']
        self.assertEqual(s['break_days'], 3)
        self.assertEqual(s['break_over_count'], 1)
        self.assertEqual(s['break_over_minutes'], 30)
        self.assertEqual(s['break_shift_count'], 1)
        self.assertEqual(s['late_count'], 0, 'un retour de pause tardif n\'est pas un retard du matin')
        line = next(j for j in result['journal'] if j['day'] == '2026-10-04')
        self.assertEqual((line['break']['start'], line['break']['end'], line['break']['over']),
                         ('12:30', '14:00', 30))
        self.assertIn('1 pause(s) déjeuner dépassée(s) — 30 min en trop',
                      [a['title'] for a in result['alerts']])
        # Pénalité de pause : 0 par défaut, réglable.
        self.assertEqual(s['score'], 100)
        self.env.company.dz_score_break_penalty = 2
        s = self.analysis.analyse(self.employee, date(2026, 10, 4), date(2026, 10, 6))['summary']
        self.assertEqual(s['score'], 98)

    @freeze_time('2026-10-20 12:00:00')
    def test_single_segment_break_not_measured(self):
        self._day_single = self.env['hr.attendance'].create({
            'employee_id': self.employee.id,
            'check_in': datetime(2026, 10, 4, 7, 0), 'check_out': datetime(2026, 10, 4, 16, 0)})
        s = self.analysis.analyse(self.employee, date(2026, 10, 4), date(2026, 10, 4))['summary']
        self.assertEqual(s['break_days'], 0)
        self.assertEqual(s['break_over_count'], 0)
