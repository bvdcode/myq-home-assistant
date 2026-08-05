# Contributing

Contributions are welcome through GitHub issues and pull requests.

## Development setup

Use Python 3.14 or newer and install the test dependencies:

```bash
python -m pip install -r requirements_test.txt
```

Before opening a pull request, run:

```bash
ruff check .
ruff format --check .
mypy custom_components/myq tests
pytest
```

Keep changes focused and include tests for new behavior. Never include account
credentials, verification codes, OAuth tokens, device serial numbers, or raw
service responses in issues, fixtures, commits, or logs.

Tests must mock door commands. Do not use a physical garage door for automated
or repeatable test runs.
