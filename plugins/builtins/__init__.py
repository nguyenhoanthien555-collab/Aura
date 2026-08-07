"""
Bundled plugins.

One plugin, and it exists to be an example as much as to be useful:

    session_stats  counts turns, and reports them through a tool

Adding a plugin means adding a module here with a `PLUGIN` instance, a
`plugin()` factory or a Plugin subclass. It does not mean touching the
manager - discovery already looks in this package.

A plugin in here is discovered but not enabled. Nothing runs until its
name appears in `plugins.enabled`, which is the same two-lock shape tools
use: the system has to be on, and each plugin has to be named.
"""
