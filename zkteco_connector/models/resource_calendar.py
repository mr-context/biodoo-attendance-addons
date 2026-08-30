# -*- coding: utf-8 -*-
from odoo import models, fields


class ResourceCalendar(models.Model):
    _inherit = 'resource.calendar'

    zkteco_use_punch_state = fields.Boolean(
        string='Punch state du device obligatoire',
        default=True,
        help="ON  : on fait confiance à la touche choisie sur la pointeuse "
             "(entrée / sortie / pause). Si la pointeuse n'a pas de touche "
             "(reconnaissance faciale/paume sans touche), on retombe sur la "
             "déduction automatique.\n"
             "OFF : on ignore la touche et on déduit automatiquement à partir "
             "des horodatages et de l'horaire : 1er pointage = entrée, puis "
             "alternance entrée/sortie, les trous = pauses.",
    )
    zkteco_break_tolerance = fields.Integer(
        string='Tolérance pause (min)',
        default=10,
        help="Marge en minutes autour de la pause allouée avant de signaler un "
             "écart. Ex. allouée 60 min + tolérance 10 → une pause entre 50 et "
             "70 min est « conforme » ; au-delà = dépassement, en deçà = écourtée.",
    )