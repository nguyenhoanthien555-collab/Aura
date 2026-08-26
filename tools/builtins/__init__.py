"""
Bundled tools.

One module per area, and a tool class per thing the owner might
authorise. Measured rather than remembered - `tools/factory.py` decides
which of these are actually built on a given machine:

    clock       SAFE       current_time
    memory      SAFE       remember - writes a fact about the owner into
                           Aura's own database and sends it nowhere
    filesystem  SENSITIVE  read_file, list_directory
                DANGEROUS  write_file, append_to_file, delete_file,
                           create_directory
    system      SENSITIVE  system_information, list_processes
    desktop     SENSITIVE  list_windows...
                DANGEROUS  ...and focus_window, which brings one to the
                           front
    vision      SENSITIVE  describe_screen, on request rather than the
                           ambient line that rides along with every
                           turn. DANGEROUS instead when the processor
                           chain can upload the frame, because sending a
                           picture of the screen to a third party is not
                           the same act as reading it
    screen      DANGEROUS  take_screenshot - writes a file
    input       DANGEROUS  move_mouse, click_mouse, type_text, press_keys
    commands    DANGEROUS  run_command, declared allow list only
    apps        DANGEROUS  open_application, allow list only

Everything above SAFE is absent from the shipped `config.yaml`'s
`tools.allowed`, which names `current_time` and `remember` and nothing
else. Registered is not enabled.

The claim this docstring used to make - that adding mouse control,
keyboard control or filesystem writes means adding a class here with the
right `risk`, registering it in `tools/factory.py`, and putting its name
in `tools.allowed`, without touching the executor - held every time.
`system` and `desktop` in section 24, the writers, `screen`, `input`,
`commands` and now `vision`: the five gates did not move for any of them.

Two things that generalise, learned from writing those two:

    * A tool that reads needs no `verify()`, because a read's
      postcondition *is* its return value and asking twice proves
      nothing. A tool that acts usually does, and `focus_window` is the
      clearest case in the repository: Windows accepts
      `SetForegroundWindow` and silently ignores it under the foreground
      lock, so "the call returned" is exactly the evidence section 11
      forbids trusting.

    * Put the OS reading behind a small source object with a mock beside
      it, the way `vision/capture.py` does. Otherwise every test asserts
      against the machine it happens to run on.
"""
