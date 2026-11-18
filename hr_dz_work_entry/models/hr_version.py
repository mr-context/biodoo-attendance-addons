"""
Extension native de hr.version pour ajouter la source 'attendance'.

Une version en source « Présences » ne passe plus par la génération
d'Odoo (qui suppose l'employé présent selon son planning) : elle est confiée
au moteur unique `hr.dz.work.entry.engine`, qui compare le planning aux
pointages et crée aussi les absences. La tâche planifiée d'Odoo « Generate
Missing Work Entries » sert ainsi de calcul quotidien.
"""

from datetime import datetime, timedelta

import pytz

from odoo import models, fields, _


class HrVersion(models.Model):
    _inherit = 'hr.version'

    # biodoo est un logiciel de pointage : les nouveaux contrats sont calculés
    # depuis les pointages. « Horaire de travail » reste disponible pour le
    # personnel dispensé de pointage (présumé présent selon son planning).
    work_entry_source = fields.Selection(
        selection_add=[('attendance', 'Présences (Pointage)')],
        ondelete={'attendance': 'set default'},
        default='attendance',
    )

    # Heures supplémentaires : jeu de règles algérien par défaut (majorées),
    # au lieu du jeu par défaut d'Odoo (taux 1).
    ruleset_id = fields.Many2one(default=lambda self: self._dz_default_overtime_ruleset())

    def _dz_default_overtime_ruleset(self):
        return (self.env.ref('hr_dz_work_entry.overtime_ruleset_dz', raise_if_not_found=False)
                or self.env.ref('hr_attendance.hr_attendance_default_ruleset', raise_if_not_found=False))

    def _dz_adopt_dz_overtime_ruleset(self):
        """Contrats restés sur le jeu par défaut d'Odoo (ou sans jeu) → jeu algérien.
        Un jeu choisi par le client n'est jamais remplacé."""
        dz = self.env.ref('hr_dz_work_entry.overtime_ruleset_dz', raise_if_not_found=False)
        odoo_default = self.env.ref('hr_attendance.hr_attendance_default_ruleset', raise_if_not_found=False)
        if not dz:
            return
        versions = self.sudo().with_context(active_test=False).search([
            '|', ('ruleset_id', '=', False), ('ruleset_id', '=', odoo_default.id if odoo_default else 0)])
        if not versions:
            return
        versions.write({'ruleset_id': dz.id})
        # Odoo ne recalcule pas les heures sup. déjà enregistrées quand le jeu
        # change : on le fait pour le mois en cours et le précédent (une paie
        # plus ancienne est close ; ses prestations validées ne bougent plus).
        since = (fields.Date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        self.env['hr.attendance'].sudo().search([
            ('employee_id', 'in', versions.employee_id.ids),
            ('check_in', '>=', since),
        ])._update_overtime()

    def _is_work_entry_source_attendance(self):
        """Retourne True si la source de CETTE version est 'attendance'."""
        self.ensure_one()
        return self.work_entry_source == 'attendance'

    def _get_work_entries_values(self, date_start, date_stop):
        """Les versions « Présences » ne produisent rien par la voie d'Odoo :
        leurs prestations sont calculées par le moteur (voir _generate_work_entries).

        Odoo 19 appelle cette méthode sur un LOT de versions ; on ne transmet à
        Odoo que les autres.
        """
        autres = self.filtered(lambda v: v.work_entry_source != 'attendance')
        if not autres:
            return []
        return super(HrVersion, autres)._get_work_entries_values(date_start, date_stop)

    def _generate_work_entries(self, date_start, date_stop, force=False):
        """Bornes UTC naïves (convention Odoo) → jours locaux, puis moteur."""
        par_pointage = self.filtered(lambda v: v.work_entry_source == 'attendance')
        autres = self - par_pointage
        result = self.env['hr.work.entry']
        if autres:
            result |= super(HrVersion, autres)._generate_work_entries(date_start, date_stop, force=force)
        if not par_pointage:
            return result

        par_pointage.write({'last_generation_date': fields.Date.today()})
        engine = self.env['hr.dz.work.entry.engine']
        for version in par_pointage:
            tz = engine._tz(version.employee_id, version)
            day_from = pytz.utc.localize(date_start).astimezone(tz).date()
            day_to = pytz.utc.localize(date_stop).astimezone(tz).date()
            # Pas de calcul dans le futur : seules les présences du jour
            # peuvent exister, et aucune absence n'est déduite avant la fin
            # de la journée.
            day_to = min(day_to, datetime.now(tz).date())
            if day_from <= day_to:
                result |= engine.sync(version.employee_id, day_from, day_to)
        return result


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def generate_work_entries_from_attendance(self, date_from, date_to):
        """Calcule les prestations depuis les pointages (moteur unique)."""
        self.env['hr.dz.work.entry.engine'].sync(self, date_from, date_to)
        return True

    def action_generate_work_entries_attendance(self):
        """Action pour générer les work entries depuis l'interface employé"""
        today = fields.Date.today()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Générer les prestations'),
            'res_model': 'hr.work.entry.compute.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_date_from': today.replace(day=1),
                'default_date_to': today,
                'default_employee_ids': self.ids,
            },
        }
