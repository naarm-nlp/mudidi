# Installation

MUDIDI requires Python 3.11+, Git, and [uv](https://docs.astral.sh/uv/). `pdftk` is required only for a multi-page PDF input.

```bash
git clone https://github.com/DavidSamuell/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary.git
cd MUDIDI
uv sync
cp .env.example .env
```

Add the API key for the provider used by your model:

```dotenv
GEMINI_API_KEY=replace-me
# OPENAI_API_KEY=replace-me
# ANTHROPIC_API_KEY=replace-me
# OPEN_ROUTER_API_KEY=replace-me
```

Credentials stay in `.env`; YAML run configurations must not contain secrets.
