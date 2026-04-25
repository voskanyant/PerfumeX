# Contributing

Run the same checks locally before pushing:

```bash
pip install -r requirements-dev.txt
npm install
make ci
```

`make ci` expects PostgreSQL to be reachable through the `POSTGRES_*` environment variables. The defaults match a local database on `127.0.0.1:5432`; override them when needed.
