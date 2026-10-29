"""
Deploying a release by service: installing it never stops anything, and each
container is recreated only when what it runs changed (its code, its
libraries, its compose definition) and only when its own guards allow.

    manifest.py  what a container's image runs, fingerprinted inside the image
    plan.py      what a release changes, container by container (pure)
    guards.py    what holds a restart back, shared with the admin's restarts
    host.py      the server's side: Docker, the database, the pins, the requests
    cli.py       watadeploy
    check.py     an image checked before anything runs on it

The host's side runs on the server's own python3 (manifest.py, guards.py,
plan.py, host.py and cli.py import nothing but the standard library);
check.py runs inside the image.
"""
