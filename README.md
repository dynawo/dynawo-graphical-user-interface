# Dynawo GUI

Web interface for [Dynawo](https://dynawo.github.io/) power-system simulations.

Built with **FastAPI** (backend) + **React/Vite** (frontend).

---

## Quick start (Linux/macOS)

Two scripts wrap the full dev setup:

```bash
./install.sh   # create the venv, install backend + frontend dependencies
./run.sh       # start both servers; open http://localhost:5173
```

`install.sh` checks prerequisites (Python ≥ 3.10, Node ≥ 18), verifies the
package registries (PyPI, npm) are reachable — stopping with a proxy hint if
not — and tells you how to install anything missing. `run.sh` launches the
backend and the Vite dev server together and stops both on `Ctrl+C`.
Environment variables (see below) are passed through, e.g.
`SESSION_TTL=7200 ./run.sh`.

To reset a broken or stale install, re-run with `--clean`, which removes the
virtual environment and the frontend dependencies before reinstalling:

```bash
./install.sh --clean   # delete .venv and frontend/node_modules,
                       # then reinstall
```

The tracked `frontend/package-lock.json` is kept (so dependencies reinstall at
their committed versions), and downloaded Dynawo versions under
`~/.config/dynawo_ihm/` are left untouched.

Prefer to run the steps yourself? See the manual instructions below.

---

## Project structure

```
dynawo_IHM/
├── api/                  # FastAPI backend
│   ├── main.py           # App entry point
│   ├── session_store.py  # Server-side session management
│   ├── dependencies.py   # FastAPI Depends() helpers
│   └── routers/          # One file per feature area
│       ├── admin.py
│       ├── auth.py
│       ├── files.py
│       ├── network.py
│       ├── parameters.py
│       ├── simulation.py
│       ├── solver.py
│       └── dynawo_version.py
├── backend/              # Shared business logic (Python library)
├── frontend/             # React + Vite frontend
│   └── src/
│       ├── pages/        # One file per page
│       └── api/          # Axios client
└── requirements.txt      # Python dependencies
```

---

## Requirements

### Python
- Python ≥ 3.10
- A virtual environment is recommended (`python3 -m venv .venv`)

### Node (React frontend only)
- Node ≥ 18 — install locally with [nvm](https://github.com/nvm-sh/nvm):
  ```bash
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  source ~/.bashrc
  nvm install --lts
  ```

---

## Install

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install
```

> **Clean install:** if you need to reset the frontend dependencies from scratch
> (e.g. after a `package.json` change or a broken `node_modules`), use:
> ```bash
> cd frontend
> rm -rf node_modules package-lock.json
> npm install
> ```
> Do **not** use `npm ci` — it skips optional native dependencies and breaks
> the Vite dev server (rolldown binding error).

---

## Run

### Backend

Run from the **repo root** (`dynawo_IHM/`):

```bash
source .venv/bin/activate
uvicorn api.main:app --reload --port 8000
```

Swagger UI available at `http://localhost:8000/docs`.

### Frontend (development)

In a second terminal:

```bash
cd frontend
npm run dev
```

Opens at `http://localhost:5173`. All `/api/*` requests are proxied to FastAPI on port 8000 — both servers must be running.

---

## Typical workflow

1. Start both servers (see above)
2. Open `http://localhost:5173`
3. Go to **Dynawo Version** → set or download a Dynawo executable
   - To use a local Dynawo install, point this to the executable inside a Dynawo **deploy** or **distribution** folder (the one containing the `ddb/` directory alongside `bin/`) — the app looks up model description files (`.desc.xml`) in `ddb/` relative to the executable path
4. Go to **Upload Files** → drag and drop your `.jobs`, `.dyd`, `.par`, `.iidm`, `.crv` files
5. Load the `.iidm` network to see the component count summary
6. Go to **Network View** → explore the network diagram and voltage levels
7. Go to **Edit Parameters** → select a dynamic model and edit its parameters; use **Revert** on individual change-log entries to undo a specific edit, or **Restore original** to go back to the uploaded file
8. Go to **Edit Solver Parameters** → adjust solver settings the same way
9. Go to **Run Simulation** → run and inspect output curves; use **Download Files** in the sidebar to export all input files and results as a ZIP

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SESSION_TTL` | `3600` | Seconds of inactivity before a session is evicted and its temp dir deleted |
| `MAX_CONCURRENT_SIMS` | `4` | Maximum number of Dynawo processes that can run simultaneously across all users |
| `MAX_CONCURRENT_PYPOWSYBL` | `4` | Maximum number of simultaneous pypowsybl-heavy operations (NAD/SLD/diff rendering, load flow runs) across all users; excess requests get an immediate 503 |
| `PYPOWSYBL_WORKERS` | `2` | Number of persistent worker processes used to run load flows (OpenLoadFlow/DynaFlow) in parallel, each with its own embedded JVM |
| `ADMIN_KEY` | — | If set, `GET /api/admin/sessions` requires this value in the `X-Admin-Key` header |
| `DYNAWO_DEFAULT_EXE` | — | Pre-set Dynawo executable path for all sessions |
| `STANDALONE` | `0` | Set to `1` to allow absolute output directory paths (local single-user use only) |

---

## Development notes

- `backend/` is the shared Python library — imported by `api/` and never duplicated
- Sessions are anonymous and auto-created on first request; each browser tab gets its own isolated workspace
- Session data (uploaded files, network object, run history) lives in memory and is cleaned up after `SESSION_TTL` seconds of inactivity

---

## Get involved!

Dynawo GUI is an open-source project and as such, questions, discussions, feedbacks and more generally any form of contribution are very welcome and greatly appreciated! For further informations about contributing guidelines, please refers to the [contributing documentation](https://github.com/dynawo/.github/blob/master/CONTRIBUTING.md).

---

## Maintainers

Dynawo GUI is currently maintained by the following people in RTE:

* Gilles Aouizerate, [gilles.aouizerate@rte-france.com](mailto:gilles.aouizerate@rte-france.com)
* Marco Chiaramello, [marco.chiaramello@rte-france.com](mailto:marco.chiaramello@rte-france.com)
* Julien De Sloovere, [julien.desloovere@rte-france.com](mailto:julien.desloovere@rte-france.com)
* Joy El-Feghali, [joy.elfeghali@rte-france.com](mailto:joy.elfeghali@rte-france.com)
* Baptiste Letellier, [baptiste.letellier@rte-france.com](mailto:baptiste.letellier@rte-france.com)
* Sylvestre Prabakaran, [sylvestre.prabakaran@rte-france.com](mailto:sylvestre.prabakaran@rte-france.com)
* Florentine Rosiere, [florentine.rosiere@rte-france.com](mailto:florentine.rosiere@rte-france.com)
* Thibaut Vermeulen, [thibaut.vermeulen@rte-france.com](mailto:thibaut.vermeulen@rte-france.com)

In case of questions or issues, you can also send an e-mail to [rte-dynawo@rte-france.com](mailto:rte-dynawo@rte-france.com).
