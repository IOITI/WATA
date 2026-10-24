"""
WATA's credentials, sealed in PostgreSQL for the services that read them.

- ``sealed``: an envelope per value, its data key wrapped for each reader (pure).
- ``keys``: one key pair per service, made on the server, never in PostgreSQL.
- ``credentials``: which credentials there are, who reads each, how each is checked.
- ``store``: the tables ``credential`` and ``credential_check``.
- ``loader``: what a service reads of them at its start.
- ``writer``, ``tokens``: writing one, rotating a token.
- ``migrate``: the move from their places of before, once.

Not ``secrets`` (the standard module) nor ``credentials`` (the admin's own
sign-in, src/admin/auth.py).
"""
