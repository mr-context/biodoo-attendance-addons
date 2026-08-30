# -*- coding: utf-8 -*-
"""Déduplique les pointages bruts avant l'ajout de la contrainte unique
(device_id, pin, timestamp). Sans ça, l'ajout de la contrainte échouerait sur
les doublons hérités du dispatch NATS concurrent.

On garde la ligne d'id le plus petit (la première insérée) et on reporte
attendance_id/employee_id si le survivant ne les a pas.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Reporter l'employé / le pointage résolu vers le survivant (plus petit id)
    # quand le survivant ne les porte pas mais qu'un doublon oui.
    cr.execute("""
        WITH ranked AS (
            SELECT id, device_id, pin, timestamp,
                   MIN(id) OVER (PARTITION BY device_id, pin, timestamp) AS keep_id
            FROM zkteco_device_attlog
        ),
        dups AS (SELECT id, keep_id FROM ranked WHERE id <> keep_id)
        UPDATE zkteco_device_attlog k
           SET employee_id = COALESCE(k.employee_id, d.employee_id),
               attendance_id = COALESCE(k.attendance_id, d.attendance_id)
          FROM dups
          JOIN zkteco_device_attlog d ON d.id = dups.id
         WHERE k.id = dups.keep_id
    """)

    cr.execute("""
        DELETE FROM zkteco_device_attlog a
         USING (
            SELECT id,
                   MIN(id) OVER (PARTITION BY device_id, pin, timestamp) AS keep_id
              FROM zkteco_device_attlog
         ) r
         WHERE a.id = r.id AND r.id <> r.keep_id
    """)
    _logger.info("[zkteco] pré-migration: doublons de pointages bruts supprimés (%d)", cr.rowcount)
