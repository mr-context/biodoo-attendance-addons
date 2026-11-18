# core_nats — le bus des pointeuses dans Odoo

Abonne Odoo aux sujets NATS du bridge et distribue chaque message au modèle qui
le traite.

## Garanties

- **Consommateur JetStream durable, mode pull**, partagé par tous les workers :
  un message est traité une fois, puis acquitté.
- Échec → relivré avec un délai qui double (max 300 s) ; après 8 essais ou si le
  message est malformé → **Messages en échec** (`nats.dead.letter`), relançables.
- Sujets « core » (sans flux) : abonnement en groupe, un seul worker reçoit.
- Compteurs en base (`nats.subscription.message_count`, incréments SQL).
- Jeton : fiche du serveur NATS, sinon `nats_token` d'`odoo.conf`.

## Écrire un traitement

```python
class MonHandler(models.AbstractModel):
    _name = 'mon.handler'
    _inherit = 'nats.handler'
    _nats_subjects = ['mon.sujet.>']
    _nats_schema = {'mon.sujet.>': {'SerialNumber': str}}   # sinon : message en échec

    def handle_nats_event(self, subject, payload):
        ...   # lever une exception = relivraison ; ne rien attraper en silence
```

## Tests

`--test-tags /core_nats` : reconnexion, repli puis retour en JetStream, messages
en échec, schéma, relance.
