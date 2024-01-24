# Contributing to Moonraker

Prior to submitting a pull request prospective contributors must read this
entire document.  Care should be taken to [format git commits](#git-commit-format)
correctly.  This eases the review process and provides the reviewer with
confidence that the submission will be of sufficient quality.

Prospective contributors should consider the following:

- Does the contribution have significant impact?  Bug fixes to existing
  functionality and new features requested by 100+ users qualify as
  items of significant impact.
- Has the submission been well tested?  Submissions with substantial code
  change must include details about the testing procedure and results.
- Does the submission include blocking code?  Moonraker is an asynchronous
  application, thus blocking code must be avoided.
- If any dependencies are included, are they pure python?  Many low-powered SBCs
  running Armbian do not have prebuilt wheels and are not capable of building wheels
  themselves, thus breaking updates on these systems.
- Does the submission change the API?  If so, could the change potentially break
  frontends using the API?
- Does the submission include updates to the documentation?

When performing reviews these are the questions that will be asked during the
initial stages.

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
[developer certificate of origin](https://developercertificate.org/)
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
the type cannot be inferred. Moonraker uses `mypy` version 1.5.1 for static
type checking with the following options:

  - `--ignore-missing-imports`
  - `--follow-imports=silent`

No line in the source code should exceed 88 characters.  Be sure there is no
trailing whitespace.  To validate code before submission one may use
`flake8` version 6.1.0 with the following options:

  - `--ignore=E226,E301,E302,E303,W503,W504`
  - `--max-line-length=88`
  - `--max-doc-length=88`

Generally speaking, each line in submitted documentation should also be no
longer than 88 characters, however there are situations where this isn't
possible, such as long hyperlinks or example return values.

Avoid peeking into the member variables of another class.  Use getters or
properties to access object state.
