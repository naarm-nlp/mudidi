# Installation

Choose the installation that matches how you plan to use MUDIDI:

- **Web dashboard:** Docker is recommended on macOS, Windows, and Linux. Follow
  the [Docker dashboard setup](../production/local-web-app.md#docker-recommended).
- **CLI and YAML:** native [uv](https://docs.astral.sh/uv/) installation is
  recommended. It requires Python 3.11+ and Git; Windows uses WSL2.

## CLI and YAML installation with uv

Install uv on macOS, Linux, or WSL2:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then clone MUDIDI and reproduce its locked environment:

```bash
git clone https://github.com/DavidSamuell/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary.git
cd MUDIDI
uv sync --frozen
cp .env.example .env
```

Install `pdftk-java` only when processing a multi-page source PDF:

```bash
# Ubuntu or WSL2
sudo apt install -y pdftk-java

# macOS with Homebrew
brew install pdftk-java
```

Add the API key for the provider used by your model:

```dotenv
GEMINI_API_KEY=replace-me
# OPENAI_API_KEY=replace-me
# ANTHROPIC_API_KEY=replace-me
# OPEN_ROUTER_API_KEY=replace-me
```

Credentials stay in `.env`; YAML run configurations must not contain secrets.
Continue with the [CLI quickstart](quickstart.md).
