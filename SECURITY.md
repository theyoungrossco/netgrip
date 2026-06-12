# Security Policy

NetGrip executes commands as root on machines you point it at, so security
reports are taken seriously.

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/theyoungrossco/netgrip/security/advisories/new)
rather than opening a public issue. You should receive a response within a
week.

## Threat model notes

- NetGrip never invents commands at apply time: every mutation is built as
  an argv list, shown to the user verbatim, shell-quoted with `shlex`, and
  executed as one batch. User-supplied values (names, addresses) are
  validated before they reach a plan.
- Remote management shells out to the system `ssh` client in BatchMode, so
  host key checking and your `~/.ssh/config` policies apply unchanged.
- Local escalation uses `sudo -n` or `pkexec`; NetGrip never stores or
  prompts for passwords itself.

## Supported versions

Only the latest release receives fixes while the project is pre-1.0.
