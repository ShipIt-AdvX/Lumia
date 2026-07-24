"""Lumia local brain (backend).

The original heavy desktop daemon was removed from the repo; this package is a
lightweight, single-process re-implementation of the "PC local brain":

* coding-time limiting with a one-time daily delay,
* activity (foreground dev app) tracking,
* eat / sleep / move reminders + sedentary detection,
* idea capture endpoints (matches HARDWARE_PROTOCOL.md),
* today's git commits as an "achievement wall".
"""

__version__ = "0.1.0"
