"""Mise à jour 19.0.1.1.0 : moteur unique de prestations, politiques de présence,
heures supplémentaires selon les règles d'Odoo (jeu algérien par défaut)."""
from odoo import api, SUPERUSER_ID
from odoo.addons.hr_dz_work_entry import _dz_setup


def migrate(cr, version):
    _dz_setup(api.Environment(cr, SUPERUSER_ID, {}))
