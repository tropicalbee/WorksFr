# Security

## Reporting a problem

Use GitHub's private vulnerability reporting on github.com/tropicalbee/WorksFr (Security tab, "Report a vulnerability"). If that is not available to you, open an issue that says only that there is a security problem, and leave out any exploit details. Don't post details in a public issue.

## What WorksFr protects against

- Tutorial code is untrusted. It runs only inside a Docker container, never in the backend process or on your host.
- The container runs as a non-root user with dropped capabilities and no network while steps run.
- Each container has limits on memory, CPU, processes and file size, and steps and whole checks have timeouts.
- URL fetches refuse private, loopback, link-local and other non-public addresses.
- The backend is meant for local use.

## Known limits

- A tutorial written to fool the checker can make its own steps look like they work, the same way code that hides its errors in try/except does. "Runs clean" is not proof of correct output.
- The backend has no login. Don't expose it to a network. Once SEC-BIND lands, requests from other machines are refused unless `API_TOKEN` is set.
- The optional GPU setting gives tutorial code access to the GPU driver. Turn it on only on a machine where you accept that.
- The optional video download means you are responsible for following YouTube's terms.
- Docker is not a perfect security boundary.
