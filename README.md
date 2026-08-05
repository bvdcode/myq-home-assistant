# MyQ for Home Assistant

[![CI](https://github.com/bvdcode/myq-home-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/bvdcode/myq-home-assistant/actions/workflows/ci.yml)
[![HACS](https://github.com/bvdcode/myq-home-assistant/actions/workflows/hacs.yml/badge.svg)](https://github.com/bvdcode/myq-home-assistant/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/bvdcode/myq-home-assistant/actions/workflows/hassfest.yml/badge.svg)](https://github.com/bvdcode/myq-home-assistant/actions/workflows/hassfest.yml)

MyQ connects Chamberlain and LiftMaster garage-door openers to Home Assistant
through the MyQ residential cloud service.

The integration provides:

- configuration entirely through the Home Assistant user interface;
- email or SMS verification during sign-in;
- automatic session renewal without repeated verification codes;
- discovery of every garage door attached to the account;
- current door state and cloud availability;
- open and close controls;
- vacation mode, work-light, fault, cycle-count, and activation diagnostics;
- cloud polling every 30 seconds.

## Requirements

- Home Assistant 2026.7.4 or newer;
- a MyQ residential account with at least one connected garage-door opener.

The initial release has been tested with a United States MyQ account. Reports
from other regions are welcome.

## Installation

The integration can be installed through HACS as a custom repository.

1. Open **HACS**.
2. Select **Integrations**.
3. Open the menu and select **Custom repositories**.
4. Add `https://github.com/bvdcode/myq-home-assistant` as an **Integration**
   repository.
5. Install **MyQ** and restart Home Assistant.

## Configuration

1. Open **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **MyQ**.
4. Enter the email address and password used by the MyQ app.
5. Select email or SMS delivery and enter the six-digit verification code.

The password is used only during sign-in and is not stored. Home Assistant
stores the issued OAuth tokens in the config entry and refreshes them
automatically. Reauthentication is requested only when MyQ invalidates the
stored authorization.

## Safety

Opening or closing a garage door can cause injury or property damage. Keep the
door area clear and maintain the opener's photo eyes, obstruction detection,
and other safety equipment. Do not use remote control where unattended
operation is prohibited.

## Development

Use Python 3.14 or newer and install the test dependencies:

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
mypy custom_components/myq tests
pytest
```

## Project status

This is a community-maintained project and is not affiliated with, endorsed by,
or associated with The Chamberlain Group LLC. MyQ, Chamberlain, and LiftMaster
are trademarks of their respective owners.
