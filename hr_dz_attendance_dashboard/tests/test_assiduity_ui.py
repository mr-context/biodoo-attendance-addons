"""L'écran d'analyse s'ouvre dans un vrai navigateur, sans erreur JS."""

from datetime import date, datetime, timedelta

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'dz_assiduity')
class TestAssiduityUi(HttpCase):

    def test_employee_assiduity_screen(self):
        calendar = self.env['resource.calendar'].create({
            'name': 'Dim-Jeu (écran assiduité)', 'tz': 'Africa/Algiers',
            'attendance_ids': [
                (0, 0, {'name': 'Journée', 'dayofweek': d, 'hour_from': 8, 'hour_to': 16,
                        'day_period': 'morning'})
                for d in ('6', '0', '1', '2', '3')],
        })
        employee = self.env['hr.employee'].create({
            'name': 'Employé écran', 'resource_calendar_id': calendar.id, 'tz': 'Africa/Algiers',
        })
        employee.version_id.write({
            'contract_date_start': date(2026, 1, 1), 'resource_calendar_id': calendar.id,
            'work_entry_source': 'attendance',
        })
        today = date.today()
        for offset in range(1, 15):
            day = today - timedelta(days=offset)
            if day.weekday() in (4, 5):
                continue
            base = datetime(day.year, day.month, day.day) - timedelta(hours=1)
            self.env['hr.attendance'].create({
                'employee_id': employee.id,
                'check_in': base + timedelta(hours=8, minutes=offset * 2),
                'check_out': base + timedelta(hours=16),
            })
        employee.job_title = 'Opérateur'
        code = """
            const until = (sel) => new Promise((resolve, reject) => {
                const t0 = Date.now();
                (function poll() {
                    const el = document.querySelector(sel);
                    if (el) return resolve(el);
                    if (Date.now() - t0 > 15000) return reject(new Error('introuvable : ' + sel));
                    setTimeout(poll, 100);
                })();
            });
            (async () => {
                // Le vrai chemin : le bouton « Assiduité » de la fiche employé.
                const button = await until('button[name=action_open_assiduity_analysis]');
                button.click();
                await until('.o_biodoo_as_score_value');
                if (document.querySelector('.o_error_dialog')) {
                    throw new Error("boîte d'erreur affichée");
                }
                await until('.o_biodoo_as_cal_cell.o_biodoo_cal_present');
                const charts = [...document.querySelectorAll('.o_biodoo_assiduity canvas')];
                if (charts.length < 3 || !charts.every((c) => window.Chart.getChart(c))) {
                    throw new Error('graphiques non rendus');
                }
                // Changement de période : l'écran se recharge sans erreur.
                const select = document.querySelector('.o_biodoo_as_period select');
                select.value = 'last12';
                select.dispatchEvent(new Event('change'));
                await new Promise((r) => setTimeout(r, 1500));
                await until('.o_biodoo_as_months_compact');
                console.log('test successful');
            })().catch((e) => console.error(e.message));
        """
        self.browser_js('/odoo/employees/%d' % employee.id, code,
                        ready="document.querySelector('button[name=action_open_assiduity_analysis]')",
                        login='admin', timeout=90)
