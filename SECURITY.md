# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include student
data, credentials, session tokens, ID-card images, server addresses, or exploit
details in public discussion. Contact the repository owner privately through
GitHub Security Advisories. Include the affected version, impact, and minimal
reproduction steps with all personal data removed.

## Supported version

Security fixes target the latest commit on `main`. Older APKs may be blocked by
the server's minimum-supported-build policy when a fix requires a client update.

## Deployment requirements

- Keep `.env`, databases, logs, APKs, verification artefacts, signing keys and
  operator credentials outside Git.
- Set `LOYOLA_ENVIRONMENT=production`; startup then requires HTTPS, debug mode
  off, a unique session secret, and a valid Fernet media key.
- Run the service with a dedicated least-privilege account behind a TLS reverse
  proxy. Restrict the database and application listener to trusted interfaces.
- Encrypt and tightly permission verification artefacts, retain them only as
  long as required, and never use production student data in tests.
- Rotate any credential immediately if it is exposed, then purge it from Git
  history before publishing.

## Repository hygiene

Before each release, run the test suite, dependency audit, and a secret scanner
over both the working tree and Git history. Generated releases belong in the
server-side release directory or a release host, not in source control.
