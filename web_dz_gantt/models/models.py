from collections import defaultdict

from lxml.builder import E

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import OrderedSet


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def _get_default_gantt_view(self):
        """Vue Gantt par défaut : premiers champs de début et de fin trouvés."""
        start = next((n for n in ('date_start', 'date_from', 'start', 'date_begin', 'date')
                      if n in self._fields), None)
        stop = next((n for n in ('date_stop', 'date_end', 'date_to', 'stop', 'date_deadline', 'date')
                     if n in self._fields), None)
        if not start or not stop:
            raise UserError(_("Indiquez date_start et date_stop pour afficher %s en Gantt.",
                              self._description))
        return E.gantt(string=self._description, date_start=start, date_stop=stop)

    @api.model
    def get_gantt_data(self, domain, groupby, read_specification, limit=None, offset=0,
                       unavailability_fields=None, progress_bar_fields=None,
                       start_date=None, stop_date=None, scale=None, scope_domain=None):
        """Données d'une vue Gantt, en un seul appel.

        :param domain: domaine (période comprise, construite par le client)
        :param groupby: champs de regroupement des lignes, du plus haut au plus bas
        :param read_specification: spécification web_read des barres
        :param unavailability_fields: champs de regroupement pour lesquels
            griser les périodes indisponibles (voir _gantt_unavailability)
        :param progress_bar_fields: champs de regroupement qui portent une
            barre de progression (voir _gantt_progress_bar)
        :param start_date, stop_date: période affichée, en UTC « AAAA-MM-JJ HH:MM:SS »
        :param scale: day, week, month ou year
        :param scope_domain: domaine de la recherche seule, sans la période
            (pour `_gantt_extra_groups` : quelles lignes ajouter)
        :return: {
            'groups': [{<champ>: <valeur formatée>, ..., '__record_ids': [ids]}],
            'length': nombre total de groupes,
            'records': [<web_read>],
            'unavailabilities': {champ: {res_id: [{'start', 'stop'}]}},
            'progress_bars': {champ: {res_id: {'value', 'max_value', ...}}},
        }
        """
        records = self.search_fetch(domain, list(read_specification))
        order = {rid: index for index, rid in enumerate(records._ids)}
        start = fields.Datetime.to_datetime(start_date) if start_date else None
        stop = fields.Datetime.to_datetime(stop_date) if stop_date else None
        if groupby:
            groups = self.formatted_read_group(domain, groupby, ['id:array_agg'])
            extra = self._gantt_extra_groups(
                domain if scope_domain is None else scope_domain, groupby, groups, start, stop)
            if extra:
                for group in extra:
                    group['id:array_agg'] = group.pop('__record_ids', [])
                groups = sorted(groups + extra, key=lambda g: self._gantt_group_sort_key(g, groupby))
            length = len(groups)
            if offset or limit:
                groups = groups[offset:(offset + limit) if limit else None]
        else:
            groups = [{'id:array_agg': list(records._ids)}]
            length = 1

        wanted = OrderedSet()
        for group in groups:
            ids = sorted((rid for rid in group.pop('id:array_agg') or [] if rid in order),
                         key=order.__getitem__)
            group['__record_ids'] = ids
            group.pop('__extra_domain', None)
            group.pop('__count', None)
            wanted.update(ids)
        shown = records.browse(list(wanted)) if (offset or limit) else records

        def res_ids(field):
            found = OrderedSet()
            for group in groups:
                value = group.get(field)
                if isinstance(value, (list, tuple)) and value:
                    found.add(value[0])
            return list(found)

        return {
            'groups': groups,
            'length': length,
            'records': shown.web_read(read_specification),
            'unavailabilities': {
                field: self._gantt_unavailability(field, res_ids(field), start, stop, scale)
                for field in unavailability_fields or []},
            'progress_bars': {
                field: self._gantt_progress_bar(field, res_ids(field), start, stop)
                for field in progress_bar_fields or []},
        }

    @api.model
    def _gantt_extra_groups(self, domain, groupby, groups, start, stop):
        """Lignes à afficher même sans enregistrement (ex. tous les employés
        sous contrat). À redéfinir. Retour : [{<champ>: <valeur formatée>,
        '__record_ids': []}]."""
        return []

    @api.model
    def _gantt_group_sort_key(self, group, groupby):
        """Ordre des lignes une fois des lignes ajoutées : par libellé."""
        key = []
        for spec in groupby:
            value = group.get(spec)
            label = value[1] if isinstance(value, (list, tuple)) and len(value) > 1 else value
            key.append((label is False or label is None, str(label or '').lower()))
        return key

    @api.model
    def _gantt_unavailability(self, field, res_ids, start, stop, scale):
        """Périodes indisponibles (grisées) par valeur du champ de regroupement.

        À redéfinir. Retour : {res_id: [{'start': datetime UTC, 'stop': datetime UTC}]}.
        Une case n'est grisée que si elle est entièrement couverte.
        """
        return {}

    @api.model
    def _gantt_progress_bar(self, field, res_ids, start, stop):
        """Barre de progression par valeur du champ de regroupement.

        À redéfinir. Retour : {res_id: {'value': x, 'max_value': y}} ; clés
        facultatives : 'label' (texte affiché), 'warning' (texte si dépassement).
        """
        return {}
