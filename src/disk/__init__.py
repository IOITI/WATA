"""
The server's disk: what WATA takes of it, sampled every hour, and where it is going.

- ``collect`` runs on the host (``watadisk``, a systemd timer made by the install script), as root, on the
  server's own python3 with nothing WATA installs: it measures the filesystems, WATA's folders, Docker and
  PostgreSQL, and keeps 14 days of samples in ``/app/wata/var/lib/disk`` (``samples.jsonl``, ``latest.json``).
- ``kinds`` says what each kind of data is, from a sample; ``levels`` judges the free space against the alarm
  levels (``reporting.disk_*``); ``projection`` says where each kind goes under given retentions.

Everything here is the standard library alone: the admin and the scheduler import ``kinds``, ``levels`` and
``projection``; nothing imports ``collect``, which only the host runs.
"""
