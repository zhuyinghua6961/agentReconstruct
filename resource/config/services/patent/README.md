# Patent Config

`config.shared.env` in this directory is the default runtime template loaded by
`patent/scripts/start_gunicorn.sh`.

The patent service should own only patent QA execution configuration here,
including whether it uses local embedding, patent-specific LLM credentials, and
durable mode settings.
