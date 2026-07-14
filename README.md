# MUDIDI

**[Read the MUDIDI documentation](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/)**

MUDIDI digitizes scanned multilingual dictionaries with language models. It first creates a faithful page transcription and then converts that transcription into [SIL Toolbox MDF](https://software.sil.org/toolbox/) lexicon records.

```text
dictionary pages → Stage 1 OCR → Stage 2 parse rules → MDF records
```

## Installation

MUDIDI supports Linux, macOS, and Windows. Docker is recommended for the web
dashboard. Native installation with [uv](https://docs.astral.sh/uv/) is
recommended for CLI and YAML workflows; it requires Python 3.11+, with native
Windows usage supported through WSL2.

### Web dashboard with Docker (recommended)

Docker runs MUDIDI in the same reproducible Linux environment on macOS,
Windows, and Linux without requiring a separate Python, uv, or `pdftk`
installation. Install [Docker Desktop](https://docs.docker.com/desktop/) on
macOS or Windows, or Docker Engine with the Compose plugin on Linux.

Before running MUDIDI, make sure Docker is running: start Docker Desktop on
macOS or Windows, or start the Docker daemon on Linux, and wait until the
Docker engine is ready. You can verify it with `docker info`.

Then clone the repository, open a terminal in it, and run:

```bash
docker compose up --build
```

Open <http://localhost:8000> in a browser. Stop the app with `Ctrl+C`, or run
`docker compose down` from another terminal. Dashboard settings, encrypted API
credentials, presets, uploaded files, and generated outputs persist in the
local `mudidi-data/` directory. This directory is excluded from Git; keep it
private and back it up as one unit.

The Compose configuration publishes MUDIDI only on `127.0.0.1`, so other
computers on the network cannot connect to it. Do not change this binding to a
public interface: the local dashboard is not designed as a multi-user or
internet-facing service.

See the
**[local web application guide](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/production/local-web-app/)**
for startup, shutdown, logs, persistence, and troubleshooting.

### Web dashboard with uv

For a native Python installation instead of Docker, install the web extra and
start the dashboard:

```bash
uv sync --frozen --extra web
uv run mudidi web
```

MUDIDI opens <http://127.0.0.1:8000>. Use `--no-browser` to prevent it from
opening a browser automatically, or `--port 8080` to choose another local port.

### CLI and YAML workflows with uv (recommended)

### Install uv on macOS or Linux

Use the official standalone installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart the terminal if requested by the installer, then verify the command is
available:

```bash
uv --version
```

### Install uv on Windows through WSL2

MUDIDI does not currently support native Windows PowerShell. First install
WSL2 with Ubuntu from an Administrator PowerShell terminal:

```powershell
wsl --install -d Ubuntu
```

Restart Windows if requested, open the Ubuntu application, and install uv from
the WSL terminal:

```bash
sudo apt update
sudo apt install -y curl git
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install MUDIDI

Clone the repository and reproduce its locked Python environment:

```bash
git clone https://github.com/DavidSamuell/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary.git MUDIDI
cd MUDIDI
uv sync --frozen
```

Install `pdftk` only when processing a multi-page source PDF. On Ubuntu or
WSL2, run `sudo apt install -y pdftk-java`; on macOS with Homebrew, run
`brew install pdftk-java`.

## API setup

For the web dashboard, enter any of the four supported keys directly under
**API credentials** on **New Run**: Gemini, OpenAI, Anthropic, or OpenRouter.
Click **Save key** beside the credential you entered. Inputs are masked and can
be revealed explicitly with the eye button. Keys persist across restarts as
encrypted ciphertext in `mudidi-web.sqlite3`; the encryption key is stored
separately as `.credential-key` in the dashboard data directory. Keep the whole
data directory private and backed up together.

LiteLLM does not require its own API key when MUDIDI uses it directly. It uses
the key belonging to the provider selected by the model identifier. A LiteLLM
virtual key is needed only when connecting to a separately hosted LiteLLM Proxy.

CLI and YAML workflows continue to read provider keys from `.env` or the
process environment. Copy the example file and add the key used by your model:

```bash
cp .env.example .env
```

```text
GEMINI_API_KEY=replace-me
# OPENAI_API_KEY=replace-me
# ANTHROPIC_API_KEY=replace-me
# OPEN_ROUTER_API_KEY=replace-me
```

Never place credentials in YAML run configuration.

## Quick end-to-end inference

Process a directory containing page images or individual page PDFs:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary
```

Inspect the selected defaults without making API calls:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary \
  --dry-run
```

Enable bounded verifier-rewriter retries directly when needed:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary \
  --stage1-agentic \
  --stage2-agentic
```

Outputs are written under:

```text
outputs/my-dictionary/
├── resolved_config.json
├── mdf_parsing_guide.json
├── stage-1/page_N/page_N_stage1_flat.txt
└── stage-2/page_N/page_N.mdf.txt
```

For repeatable and advanced runs, use a validated YAML configuration:

```bash
uv run mudidi config validate examples/configs/production/directory-inference.yaml
uv run mudidi run --config examples/configs/production/directory-inference.yaml
```

For every available option, see the
**[CLI flag reference](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/reference/cli/)**
and the
**[complete YAML field reference](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/reference/config/)**.

## Documentation

The public documentation is available at
**[davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/)** and
separates two primary workflows:

- **Production Inference** — digitize your own PDF or page directory.
- **Benchmarking & Evaluation** — reproduce experiments against the MUDIDI dataset.

The site is rebuilt and deployed automatically from `main` with GitHub Pages.
Documentation sources are under [`docs/`](docs/), and canonical configurations
are under [`examples/configs/`](examples/configs/).

## Dataset and paper

The benchmark contains 30 public-domain multilingual dictionaries. The dataset is available through [Hugging Face](https://huggingface.co/datasets/DavidSamuell/mudidi).

```bibtex
@misc{mudidi2026,
  title         = {{MUDIDI: A Two-Stage Framework for Multilingual Dictionary Digitization with Language Models}},
  author        = {Setiawan, David and Khishigsuren, Temuulen and Agarwal, Milind and Pit, Pagnarith and Mahmudi, Aso and Vylomova, Ekaterina},
  year          = {2026},
  eprint        = {2606.09435},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CL},
  doi           = {10.48550/arXiv.2606.09435},
  url           = {https://arxiv.org/abs/2606.09435}
}
```
