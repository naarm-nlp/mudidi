# Public documentation deployment

MUDIDI's documentation is published for everyone at
[davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/).

The `Documentation` GitHub Actions workflow validates pull requests and, after
a change reaches `main`, builds MkDocs and deploys the generated static site to
GitHub Pages. The generated `site/` directory is never committed.

## One-time repository setup

An administrator must select **Settings → Pages → Build and deployment →
Source: GitHub Actions** in the GitHub repository. GitHub Pages is free for
public repositories.

After the first successful deployment, add
`https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/`
to the repository's **About → Website**
field so GitHub displays the documentation link beside the project description,
as repositories such as FastAPI do.

The workflow can also be run manually from **Actions → Documentation → Run
workflow** on `main`.
