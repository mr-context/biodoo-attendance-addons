from odoo import _, fields, models

# Attributs de <gantt> qui nomment un champ du modèle.
GANTT_FIELD_ATTRS = ('date_start', 'date_stop', 'color', 'default_group_by',
                     'consolidation', 'progress_bar', 'legend_field')


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    type = fields.Selection(selection_add=[('gantt', 'Gantt')])

    def _get_view_info(self):
        return {'gantt': {'icon': 'fa fa-tasks'}} | super()._get_view_info()

    def _gantt_field_names(self, node):
        """Champs nommés par les attributs de <gantt> (plusieurs pour
        default_group_by et progress_bar, séparés par des virgules)."""
        names = []
        for attr in GANTT_FIELD_ATTRS:
            for name in (node.get(attr) or '').split(','):
                name = name.strip()
                if name:
                    names.append(name)
        return names

    def _postprocess_tag_gantt(self, node, name_manager, node_info):
        # Le client a besoin de la description de ces champs (type date ou
        # datetime, many2one…) même s'ils ne sont pas des <field> de la vue.
        for name in self._gantt_field_names(node):
            if name in name_manager.model._fields:
                name_manager.has_field(node, name, node_info)

    def _validate_tag_gantt(self, node, name_manager, node_info):
        if not node_info['validate']:
            return
        model = name_manager.model
        for attr in ('date_start', 'date_stop'):
            if not node.get(attr):
                self._raise_view_error(_("La vue Gantt doit indiquer l'attribut « %s ».", attr), node)
        for name in self._gantt_field_names(node):
            field = model._fields.get(name)
            if not field:
                self._raise_view_error(
                    _("Le champ « %(field)s » n'existe pas sur %(model)s.", field=name, model=model._name),
                    node)
            name_manager.has_field(node, name, node_info)
        for attr in ('date_start', 'date_stop'):
            field = model._fields[node.get(attr)]
            if field.type not in ('date', 'datetime'):
                self._raise_view_error(
                    _("« %s » doit être un champ date ou date-heure.", node.get(attr)), node)
