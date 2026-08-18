# Hugging Face Space deployment

MemoryStand's Hugging Face Space is a **static mirror of `frontend/`**, not a
second application. The browser calls the same deployed Lambda API used by the
AWS Amplify site.

## Build the exact bundle

```bash
python3 scripts/build_hf_space.py --out /tmp/memorystand-hf-space
```

The output contains:

- `index.html`
- `app.js`
- `README.md` with `sdk: static`

Do not hand-edit the generated bundle. Change `frontend/`, test it, then rebuild.

## Create or update the Space

The intended repository is `tomyimkc/memorystand`.

```bash
hf auth whoami
hf repo create tomyimkc/memorystand --repo-type space --space-sdk static
hf upload tomyimkc/memorystand /tmp/memorystand-hf-space . --repo-type space
```

The public app URL is:

```text
https://tomyimkc-memorystand.static.hf.space/
```

## Security boundary

- Never put the operator secret, AWS credentials, CockroachDB connection
  string, or a private provider key in this Space.
- The demo credential is intentionally returned by the API's public
  `/health` response. The backend restricts it to the isolated demo tenant and
  returns `401` for another tenant.
- Read routes are likewise restricted to the public demo tenant unless the
  operator credential is supplied.

## Public verification

After upload, verify in a logged-out browser:

1. The page loads and reports `API reachable`.
2. The first comparison shows a closer `restart_service` memory as
   `unconfirmed` and a farther `scale_up` memory as `verified`.
3. **Run the live decision** returns `scale up`, `held for human approval`, and
   the response's actual model-call count.
4. **Inspect CockroachDB receipt** shows the recorded query and both ranked
   memories.
5. Desktop and 390 px mobile views have no horizontal overflow.

If the live writes fail only on the Space, inspect the browser's preflight and
confirm that the API still returns `Access-Control-Allow-Origin: *` and allows
`GET,POST,OPTIONS`.
