# Troubleshooting

## Tests cannot import the package

From the repository root, install the development package in the active virtual environment:

    python -m pip install -e '.[dev]'
    python -m pytest

Check that the interpreter and pip belong to the same environment with python --version and python -m pip --version.

## pytest or Ruff is missing

    python -m pip install -e '.[dev]'

Then run python -m pytest and ruff check src tests.

## Configuration rejects startup

The initial scaffold intentionally permits only simulation or read-only operation and rejects actuation-enabled configuration. Do not weaken this guard to get past a failure; inspect the test and configuration requirements first.

## A test fails on a developer machine

Record the command, Python version, operating system, complete error and commit SHA. Reproduce in a clean virtual environment before changing code.

## Hardware or MAVLink issues

This scaffold does not yet validate a real vehicle connection. Do not infer that a successful Python test proves MAVLink framing, failsafe settings or pump interlock behavior. Keep hardware disconnected and use simulation or mocks until the relevant tests and reviews exist.