from datetime import date, datetime, timedelta
import calendar
from babel.dates import format_date

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class HrEmployeePortal(CustomerPortal):
    """Extend the customer portal with HR-specific sections."""

    def _get_portal_employee(self):
        """Return the hr.employee linked to the current portal user, or False."""
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        employee = self._get_portal_employee()

        if 'attendance_count' in counters:
            attendance_count = 0
            if employee:
                attendance_count = request.env['hr.attendance'].sudo().search_count([
                    ('employee_id', '=', employee.id),
                ])
            values['attendance_count'] = attendance_count

        # Masque les sections facturation/commandes pour les employés.
        # IMPORTANT : ne pas inclure is_employee dans la réponse de la route
        # JSON-RPC /my/counters — le JS Odoo itère sur TOUTES les clés retournées
        # et cherche un élément [data-placeholder_count='xxx'] pour chacune.
        # Si la clé n'a pas de tel élément → querySelector retourne null →
        # null.textContent= … → TypeError → Promise.all rejette →
        # le spinner o_portal_doc_spinner ne disparaît jamais.
        #
        # home() appelle cette méthode avec counters=[] (liste vide).
        # /my/counters l'appelle avec la liste des compteurs demandés (non vide).
        # On n'inclut is_employee QUE pour le rendu HTML complet de la page /my.
        if not counters:
            values['is_employee'] = bool(employee)

        return values

    # -------------------------------------------------------------------------
    # /my/attendances — pointage mensuel
    # -------------------------------------------------------------------------

    @http.route(['/my/attendances'],
                type='http', auth='user', website=True)
    def portal_my_attendances(self, month=None, **kw):
        employee = self._get_portal_employee()
        if not employee:
            return request.redirect('/my')

        # --- Calcul du mois affiché ---
        today = date.today()
        if month:
            try:
                current = datetime.strptime(month, '%Y-%m').date().replace(day=1)
            except ValueError:
                current = today.replace(day=1)
        else:
            current = today.replace(day=1)

        last_day = calendar.monthrange(current.year, current.month)[1]
        month_start = datetime(current.year, current.month, 1, 0, 0, 0)
        month_end   = datetime(current.year, current.month, last_day, 23, 59, 59)

        # --- Navigation mois précédent / suivant ---
        prev_date = (current - timedelta(days=1)).replace(day=1)
        next_date = (current.replace(day=last_day) + timedelta(days=1)).replace(day=1)
        prev_month = prev_date.strftime('%Y-%m')
        next_month = next_date.strftime('%Y-%m')
        show_next  = next_date <= today.replace(day=1)

        # --- Récupération des présences ---
        attendances = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in',    '>=', month_start),
            ('check_in',    '<=', month_end),
        ], order='check_in asc')

        # --- Total heures du mois ---
        total_seconds = sum(
            int(att.worked_hours * 3600) for att in attendances if att.check_out)
        total_h = total_seconds // 3600
        total_m = (total_seconds % 3600) // 60

        lang = request.env.lang or 'fr_FR'
        current_label = format_date(current, format='MMMM yyyy', locale=lang)

        values = self._prepare_portal_layout_values()
        values.update({
            'employee':        employee,
            'attendances':     attendances,
            'current_label':   current_label.capitalize(),
            'prev_month':      prev_month,
            'next_month':      next_month,
            'show_next':       show_next,
            'total_hours_str': '%dh%02d' % (total_h, total_m),
            'page_name':       'attendances',
        })
        return request.render('hr_employee_portal.portal_my_attendances', values)
