"""
Bundled tools.

Three tools covering the three risk levels, which exist as much to be
examples as to be useful:

    clock  SAFE       runs with no approval
    files  SENSITIVE  reads user data, sandboxed to configured roots
    apps   DANGEROUS  changes the machine, allow list only

Adding mouse control, keyboard control or filesystem writes means adding
a class here with the right `risk`, registering it, and putting its name
in `tools.allowed`. It does not mean touching the executor - the gate is
already in front of it.
"""
