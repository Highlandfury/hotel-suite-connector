### Hotel Suite Control

Hotel operations, audit controls and ERPNext integration

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app hotel_suite_connector
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/hotel_suite_connector
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

agpl-3.0

## Audit foundation (v0.1.0)

This release introduces:

- Hotel control settings with enforcement disabled by default
- Configurable approval policies and approval stages
- Transaction cancellation request records
- Hotel audit and operational control roles

No ERPNext, HRMS, CRM, Payments or Kamra core files are modified.

## Operational audit checklists (v0.2.0)

This release adds:

- Reusable hotel audit checklist templates
- Controlled audit checklist execution records
- Night audit, stock audit, receiving audit and preventive-maintenance starter templates
- Per-item pass/fail results, observations, evidence and completion metadata

Approval enforcement remains disabled until explicitly configured and tested.

## Checklist usability hotfix (v0.2.1)

- Fixed browser validation blocking a new checklist before template items could be populated.
- The server remains responsible for loading template items and rejecting an empty checklist.
- No approval or transaction enforcement is enabled by this hotfix.
