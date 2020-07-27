# Contributing to Moonraker

While Moonraker exists as a service independently from Klipper, it relies
on Klipper to be useful.  Thus, the tentative plan is to eventually merge
the Moonraker application into the Klipper repo after Moonraker matures,
at which point this repo will be archived.  As such, contibuting guidelines
are near those of Klipper:

- All source files should begin with a copyright notice in the following
  format:
  ```
  # Module name and brief description of module
  #
  # Copyright (C) 2020 YOUR NAME <YOUR EMAIL ADDRESS>
  #
  # This file may be distributed under the terms of the GNU GPLv3 license
  ```
- No line in the source code or documentation should exceed 80 characters.
  Be sure there is no trailing whitespace.
- Each Commit message should be in the following format:
  ```
  module name: brief description of commit

  More detailed explanation if necessary.

  Signed-off-by:  Your Name <your email address>
  ```
- By signing off on commits, you acknowledge that you agree to the
  [developer certificate of origin](developer-certificate-of-origin).
  As with Klipper, this must contain your real name and a current
  email address.
