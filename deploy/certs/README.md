# TLS certificates

Place the HTTPS edge certificate files here:

- `fullchain.pem`: server certificate chain
- `privkey.pem`: server private key
- `rootCA.pem`: optional internal CA certificate for test/self-signed deployments

The generated development certificate is for local/intranet testing only. For
production, replace `fullchain.pem` and `privkey.pem` with the deployment
party's certificate for their final domain.

For the bundled local test domain:

```text
lifeo4.agent.test -> 172.19.14.204
```

Regenerate test files with:

```bash
bash deploy/scripts/generate_dev_tls_cert.sh lifeo4.agent.test 172.19.14.204
```
