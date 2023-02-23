# Contributing to Moonraker

While Moonraker exists as a service independently from Klipper, it relies
on Klipper to be useful.  Thus, the tentative plan is to eventually merge
the Moonraker application into the Klipper repo after Moonraker matures,
at which point this repo will be archived.  As such, contributing guidelines
are near those of Klipper:

#### New Module Contributions

All source files should begin with a copyright notice in the following format:

```python
# Module name and brief description of module
#
# Copyright (C) 2021 YOUR NAME <YOUR EMAIL ADDRESS>
#
# This file may be distributed under the terms of the GNU GPLv3 license
```

#### Git Commit Format

Commits should be contain one functional change.  Changes that are unrelated
or independent should be broken up into multiple commits.  It is acceptable
for a commit to contain multiple files if a change to one module depends on a
change to another (ie: changing the name of a method).

Avoid merge commits.  If it is necessary to update a Pull Request from the
master branch use git's interactive rebase and force push.

Each Commit message should be in the following format:

```text
module: brief description of commit

More detailed explanation of the change if required

Signed-off-by: Your Name <your email address>
```

Where:

- `module`: is the name of the Python module you are changing or parent
  folder if not applicable
- `Your Name`: Your real first and last name
- `<your email address>`: A real, reachable email address

For example, the git log of a new `power.py` device implementation might look
like the following:

```git
power: add support for mqtt devices

Signed-off-by: Eric Callahan <arksine.code@gmail.com>
```
```git
docs: add mqtt power device documentation

Signed-off-by: Eric Callahan <arksine.code@gmail.com>
```

By signing off on commits, you acknowledge that you agree to the
[developer certificate of origin](../developer-certificate-of-origin)
shown below.  As mentioned above, your signature must contain your
real name and a current email address.

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.
1 Letterman Drive
Suite D4700
San Francisco, CA, 94129

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
#### Code Style
Python methods should be fully annotated. Variables should be annotated where
the type cannot be inferred. Moonraker uses the `mypy` static type checker for
code validation with the following options:

  - `--ignore-missing-imports`
  - `--follow-imports=silent`

No line in the source code should exceed 80 characters.  Be sure there is no
trailing whitespace.  To validate code before submission one may use
`pycodestyle` with the following options:

  - `--ignore=E226,E301,E302,E303,W503,W504`
  - `--max-line-length=80`
  - `--max-doc-length=80`

Generally speaking, each line in submitted documentation should also be no
longer than 80 characters, however there are situations where this isn't
possible, such as long hyperlinks or example return values.  Documentation
isn't linted, so it

Don't peek into the member variables of another class.  Use getters or
properties to access object state.
