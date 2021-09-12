#!/bin/bash

echo "Running pycodestyle..."
pycodestyle --ignore=E226,E301,E302,E303,W503,W504 --max-line-length=80 --max-doc-length=80 moonraker scripts
echo "Running mypy..."
mypy --ignore-missing-imports --follow-imports=silent moonraker scripts
echo "Done"
