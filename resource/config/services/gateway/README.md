# Gateway Config

Gateway-local config templates belong here.

Shared infrastructure and model endpoint defaults come from `resource/config/shared/` and
are loaded before these service-local files by gateway launch scripts. Keep gateway-local
overrides here only when gateway intentionally differs from the shared default.

Gateway owns:

- conversation-file provider mode
- strict backend config flags
- frontend proxy defaults
- gateway Gunicorn worker count
- gateway-specific routing, admission, and interactive execution concurrency limits

Shared config owns service ports, backend base URLs, Redis defaults, and model endpoint
aliases that gateway-side workers may need. Process environment values still override both
shared and service-local env files. Keep tokens and Redis passwords in local secret files.
