"""Tests JS (Hoot) de la vue Gantt, lancés dans le navigateur."""

from odoo.tests import HttpCase, tagged


def hoot_id(descriptor):
    """Identifiant Hoot d'une suite (même calcul que web/tests/test_js.py)."""
    value = 0
    for char in descriptor:
        value = ((value << 5) - value + ord(char)) & 0xFFFFFFFF
    return f'{value:08x}'


@tagged('post_install', '-at_install', 'dz_gantt')
class TestGanttJs(HttpCase):

    def test_gantt_helpers(self):
        self.browser_js(
            f'/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={hoot_id("@web_dz_gantt")}',
            "", "", login='admin', timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=lambda message: '[HOOT]' not in message)
