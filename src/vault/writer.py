"""
Writing a credential: checked, sealed with the public keys of the services
that read it, and written whole. Whoever writes needs no private key; only a
reader can open what it wrote. Used by the admin, the migration and the
command lines on the server (watawebtoken).
"""

from src.vault import credentials as catalog
from src.vault import keys, sealed, store


def sealed_for_readers(name: str, value: dict, keys_dir: str = keys.KEYS_DIR) -> dict:
    """``value`` sealed for every reader of ``name``; KeyProblem when one has no public key."""
    credential = catalog.BY_NAME[name]
    publics = keys.public_keys(keys_dir)
    missing = [role for role in credential.readers if role not in publics]
    if missing:
        raise keys.KeyProblem(
            f"No public key for {', '.join(missing)}: the keys are made by the install script (python -m src.vault keygen)"
        )
    return sealed.seal(name, value, {role: publics[role] for role in credential.readers})


async def put(db, name: str, value: dict, *, by: str | None, keys_dir: str = keys.KEYS_DIR, only_if_missing=False) -> bool:
    """
    Write ``value`` as ``name``, replacing it (or only when it has no row, for
    the migration). Whether it was written. ``value`` is sealed whole; what is
    shown of it (its public parts, its last four characters) is kept beside.
    """
    from src.database.postgres import VAULT_SCHEMA_SQL

    credential = catalog.BY_NAME[name]
    envelope = sealed_for_readers(name, value, keys_dir)
    await db.execute(VAULT_SCHEMA_SQL)
    arguments = dict(
        public=catalog.public_of(name, value), sealed=envelope, readers=credential.readers,
        fingerprint=catalog.fingerprint(name, value), by=by,
    )
    if only_if_missing:
        return await store.insert_missing(db, name, **arguments)
    await store.write(db, name, **arguments)
    return True
