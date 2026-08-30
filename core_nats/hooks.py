# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """
    Called after fresh install.
    Creates the default NATS server record if none exists.
    """
    Server = env['nats.server'].sudo()
    if not Server.search([], limit=1):
        Server.create({
            'name':       'NATS Server',
            'url':        'nats://localhost:4222',
            'auto_start': True,
        })
        _logger.info("[core_nats] default NATS server record created")


def post_migrate_hook(env, version):
    """
    Called after every upgrade (version is the previous version string).
    Purges nats.subscription rows whose handler_model no longer exists
    in the current registry — avoids residue from removed connector modules.
    """
    if not version:
        return  # fresh install — post_init_hook already handled it

    Sub = env['nats.subscription'].sudo()
    all_subs = Sub.search([('handler_model', '!=', False)])
    known_models = set(env.registry.models.keys())
    orphans = all_subs.filtered(lambda s: s.handler_model not in known_models)
    if orphans:
        _logger.info(
            f"[core_nats] purging {len(orphans)} orphaned subscription(s): "
            + ", ".join(orphans.mapped('handler_model'))
        )
        orphans.unlink()


def uninstall_hook(env):
    """Stop the NATS service cleanly before uninstalling."""
    try:
        from odoo.addons.core_nats.services.nats_service import get_service, set_service
        svc = get_service()
        if svc:
            svc.stop()
            set_service(None)
            _logger.info("[core_nats] NATS service stopped on uninstall")
    except Exception as exc:
        _logger.warning(f"[core_nats] uninstall_hook: {exc}")
