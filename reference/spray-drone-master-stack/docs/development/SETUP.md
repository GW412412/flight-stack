# Local development setup

This is an initial Python scaffold. Use a dedicated virtual environment and do not install dependencies globally.

From the repository root:

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -e '.[dev]'
    python -m pytest

These commands are instructions, not evidence that they have been run on the target workstation. Record the OS and Python version when validating the environment.