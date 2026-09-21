#!/usr/bin/env python3
"""Generate the hand-authored dictionary metadata LaTeX table."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from table_utils import TableDataError, lines_to_text, parse_paths, read_delimited, write_text_atomic

SOURCE_LANGUAGES = {
    "Assyrian": "Assyrian",
    "Bengali": "Bengalese",
    "Canala (Xârâcùù)": "Canala",
    "Chepang": "Chepang",
    "Chukchi": "Chukchi",
    "Circassian (Adyghe)": "Circassian",
    "Efik": "Efik",
    "Evenki": "Evenki",
    "Georgian": "Georgian",
    "Gojri": "Gojri",
    "Greek": "Greek",
    "Gujarati": "Gujarati",
    "Iñupiatun Eskimo": "Iñupiatun Eskimo",
    "Japanese": "Japanese",
    "Kashmiri": "Kashmiri",
    "Khmer (Cambodian)": "Khmer",
    "Malay": "Malay",
    "Na (Mosuo)": "Na",
    "Nahuatl": "Nahuatl",
    "Punjabi": "Punjabi",
    "Reel": "Reel",
    "Ritharngu": "Ritharngu",
    "Sanskrit": "Sanskrit",
    "Shilluk": "Shilluk",
    "Syriac": "Syriac",
    "Telugu": "Telugu",
    "Thai": "Thai",
    "Tiri (Grand Couli)": "Tiri",
    "Vernacular Syriac": "Vernacular Syriac",
    "Yiddish": "Yiddish",
}


@dataclass(frozen=True)
class DictionaryInfo:
    """Hand-authored metadata for one dictionary."""

    source: str
    target: str
    j20: str
    egids: str
    family: str
    area: str
    characters: str
    citation: str


ROWS = (
    DictionaryInfo("Assyrian", "English", "0", "10", "Afro-Asiatic", "Eurasia", '{\\cuneiformfont \\char"12362\\char"12154\\char"12072}', r"Williams \& Northgate, 1868"),
    DictionaryInfo("Bengali", "English", "3", "1", "Indo-European", "Eurasia", r"{\bengalifont ত্ম ত জ্ঞ}", "Mendies, 1828"),
    DictionaryInfo("Canala (Xârâcùù)", "English", "0", "6a", "Austronesian", "Papunesia", r"{\ipafont mʷ ã ɨ}", "Grace, 1975"),
    DictionaryInfo("Chepang", "English", "0", "6b", "Sino-Tibetan", "Eurasia", r"{\ipafont ŋ a ʔ}", "Caughley, 2000"),
    DictionaryInfo("Chukchi", "Russian", "0", "6b", "Chukotko-Kamchatkan", "Eurasia", r"{\cyrillicfont қ ӈ ԓ}", "Inenlikei, 1982"),
    DictionaryInfo("Circassian (Adyghe)", "English, Turkish", "1", "5", "Northwest Caucasian", "Eurasia", r"{\arabicfont ش و نِه}", "Loewe, 1854"),
    DictionaryInfo("Efik", "English", "0", "3", "Atlantic-Congo", "Africa", r"{\ipafont ö ñ ë}", "Hugh, 1886"),
    DictionaryInfo("Evenki", "Russian", "0", "6b", "Tungusic", "Eurasia", r"{\cyrillicfont ӯ э̄ н}", "Vasilevish, 1958"),
    DictionaryInfo("Georgian", "Russian", "3", "1", "Kartvelian", "Eurasia", r"{\geofont ე ს თ}", "Kankava, 2001 (3rd ed)"),
    DictionaryInfo("Gojri", "English, Hindi", "0", "5", "Indo-European", "Eurasia", r"{\devanagarifont सा र णू}", r"Anjum \& Sadiq, 2021"),
    DictionaryInfo("Greek", "English", "3", "1", "Indo-European", "Eurasia", r"{\greekfont ξ λ ψ}", "Kyriakidēs, 1892"),
    DictionaryInfo("Gujarati", "English", "1", "2", "Indo-European", "Eurasia", r"{\gujaratifont ગ બ ત્તી}", "Edalji, 1863"),
    DictionaryInfo("Iñupiatun Eskimo", "English", "1", "8a", "Eskimo-Aleut", "North America", "u t m", "Seiler, 2012"),
    DictionaryInfo("Japanese", "English", "5", "1", "Japonic", "Eurasia", r"{\japanesefont フ ナ ド}", "Hepburn, 1886"),
    DictionaryInfo("Kashmiri", "English", "1", "4", "Indo-European", "Eurasia", r"{\arabicfont کہِ لٹہِ کٹَ}", "Chaltra, 1922"),
    DictionaryInfo("Khmer (Cambodian)", "English", "1", "1", "Austroasiatic", "Eurasia", r"{\khmerfont ផ្ អើ ល}", "ICC, 2012"),
    DictionaryInfo("Malay", "English", "3", "3", "Austronesian", "Eurasia", r"{\arabicfont يا غ قر}", "Howison, 1801"),
    DictionaryInfo("Na (Mosuo)", "English, Chinese, French", "0", "6b", "Sino-Tibetan", "Eurasia", r"{\ipafont ˩ ɕ ˧}", r"Michaud \& Galliot, 2018"),
    DictionaryInfo("Nahuatl", "French", "1", "6a/b", "Uto-Aztecan", "North America", r"{\ipafont Ç O T}", "Siméon, 1885"),
    DictionaryInfo("Punjabi", "English", "2", "2", "Indo-European", "Eurasia", r"{\gurmukhifont ਕੁ ਚਾ ਰੀ}", "Janvier, 1854"),
    DictionaryInfo("Reel", "English", "0", "6a", "Nilotic", "Africa", r"{\ipafont ɛ̈ ŋ ä}", "Cien et al., 2015"),
    DictionaryInfo("Ritharngu", "English", "0", "8b", "Pama-Nyungan", "Australia", r"{\ipafont ṛ č ḍ}", "Heath, 1980"),
    DictionaryInfo("Sanskrit", "English", "2", "9", "Indo-European", "Eurasia", r"{\devanagarifont क झ त}", "Yates, 1846"),
    DictionaryInfo("Shilluk", "English", "0", "5", "Nilotic", "Africa", r"{\ipafont ä r ø}", r"Ayoker \& Kur, 2016"),
    DictionaryInfo("Syriac", "English", "0", "9", "Afro-Asiatic", "Eurasia", r"{\syriacfont ܡ ܪܵ ܐ}", "Yohannan, 1900"),
    DictionaryInfo("Telugu", "English", "1", "2", "Dravidian", "Eurasia", r"{\telugufont అ వ ష్టం}", "Sankaranarayana, 1900"),
    DictionaryInfo("Thai", "Russian", "3", "1", "Tai-Kadai", "Eurasia", r"{\thaifont วั ฒ นะ}", "Morev, 1964"),
    DictionaryInfo("Tiri (Grand Couli)", "English", "0", "7", "Austronesian", "Papunesia", r"{\ipafont ɔ̃ bʷ ŋ}", "Grace, 1976"),
    DictionaryInfo("Vernacular Syriac", "Kurdish, Turkish, English", "0", "6b", "Afro-Asiatic", "Eurasia", r"{\syriacfont ܬ ܫܸ ܡܲ}", "Maclean, 1901"),
    DictionaryInfo("Yiddish", "English", "1", "9", "Indo-European", "Eurasia", r"{\hebrewfont ע ן פ}", "Harkavy, 1901"),
)


def _scripts_by_language(repo_root: Path) -> dict[str, str]:
    source = repo_root / "tables" / "results" / "dictionary_language_script_words_manual.tsv"
    rows = read_delimited(
        source,
        required_columns=("language", "script", "words", "decision"),
        delimiter="\t",
    )
    included: dict[str, list[tuple[int, str]]] = {}
    for row_number, row in enumerate(rows, start=2):
        if row["decision"] == "exclude":
            continue
        try:
            words = int(row["words"])
        except ValueError as exc:
            raise TableDataError(
                f"{source}: row {row_number}: words must be an integer, got {row['words']!r}"
            ) from exc
        included.setdefault(row["language"], []).append((words, row["script"]))
    return {
        language: ", ".join(script for _, script in sorted(values, reverse=True))
        for language, values in included.items()
    }


def render_table(repo_root: Path) -> str:
    """Render the dictionary metadata table."""
    scripts = _scripts_by_language(repo_root)
    data_lines: list[str] = []
    for row in ROWS:
        language = SOURCE_LANGUAGES[row.source]
        if language not in scripts:
            raise TableDataError(f"No non-excluded manual-TSV rows found for language: {language}")
        cells = (
            row.source,
            row.target,
            row.j20,
            row.egids,
            row.family,
            row.area,
            scripts[language],
            row.characters,
            row.citation,
        )
        data_lines.append(" & ".join(cells) + r" \\")

    lines = [
        "% Auto-generated by tables/scripts/dictionary_info_table.py -- do not edit by hand.",
        "% Hand-authored linguistic metadata; not derived from any data file in this repo.",
        r"\begin{table*}[!ht]",
        r"\centering",
        r"\small",
        r"\adjustbox{max width=\textwidth}{%",
        r"\begin{tabular}{L{2.5cm} L{1.8cm} c c L{2.6cm} L{1.7cm} L{2.6cm} L{2.4cm} L{3cm}}",
        r"\toprule",
        r"\textbf{Source} & \textbf{Target} & \textbf{J20} & \textbf{EGIDS} & \textbf{Language family} & \textbf{Area} & \textbf{Script} & \textbf{Characters} & \textbf{Citation}\\",
        r"\midrule",
        *data_lines,
        r"\bottomrule",
        r"\end{tabular}}",
        r"\caption{Languages and scripts included in the dataset and evaluation; listed alphabetically by source language. The J20 column follows the resource taxonomy of \citet{joshi-etal-2020-state}, ranging from 0 for low-resource languages to 5 for high-resource languages. The EGIDS column follows the Expanded Graded Intergenerational Disruption Scale~\cite{lewis2010assessing}, ranging from 0 for international languages to 10 for extinct languages.}",
        r"\label{tab:dictionaries-info}",
        r"\end{table*}",
    ]
    return lines_to_text(lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Generate ``dictionary_info.tex``."""
    repo_root, output_dir = parse_paths(__file__, __doc__ or "", argv)
    output_path = output_dir / "dictionary_info.tex"
    write_text_atomic(output_path, render_table(repo_root))
    print(f"Wrote {output_path} ({len(ROWS)} rows)")


if __name__ == "__main__":
    main()
