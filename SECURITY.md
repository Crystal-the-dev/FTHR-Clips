# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.0.x (alpha) | ✅ |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Send a report to: **community@fthrclips.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Your suggested fix (optional)

You'll get a response within 72 hours. If the issue is confirmed, we'll work on a fix and credit you in the release notes (unless you prefer to stay anonymous).

## Scope

Things we care about:

- Code execution via malicious clip files or settings
- Privilege escalation (the capture engine runs as the user, not root)
- Data leakage from the local clip library
- Hotkey hijacking or command injection via the Unix socket

Out of scope:

- Issues requiring physical access to the machine
- Social engineering
- Theoretical vulnerabilities without a proof of concept
