# Paper Review: MUDIDI: A Two-Stage Framework for Multilingual Dictionary Digitization with Language Models

## Paper Metadata

- **Authors**: David Setiawan, Temuulen Khishigsuren, Milind Agarwal, Pagnarith Pit, Aso Mahmudi, and Ekaterina Vylomova
- **Status**: Revised 2026 manuscript; associated with [arXiv:2606.09435](https://arxiv.org/abs/2606.09435)
- **Year**: 2026
- **Domain**: Multilingual document understanding, OCR, computational lexicography, and language-resource creation
- **Paper type**: Empirical benchmark and systems/resource paper
- **Materials audited**: The supplied 36-page PDF and the MUDIDI repository at commit `d5bf32c`, including the current evaluation artifacts. Some evaluation files in the supplied working tree are uncommitted, so the numerical audit below describes that exact working state rather than a clean, independently recoverable revision.

## Executive Summary

This paper introduces MUDIDI, a two-stage benchmark and processing framework for digitising bilingual and multilingual dictionaries. Stage 1 evaluates page transcription across 30 dictionaries and a notably broad set of scripts, including historically and computationally under-served writing systems. Stage 2 evaluates entry segmentation and assignment of SIL Toolbox MDF fields on a ten-dictionary subset. The paper compares conventional OCR, document-specialised vision-language models, and general-purpose multimodal models, and studies alphabet hints, preliminary OCR hints, dictionary introductions, MDF instructions, typography, inferred versus gold parse rules, end-to-end use of predicted transcripts, and API cost.

The task, resource, and two-stage decomposition are valuable. The paper addresses a real bottleneck in computational lexicography, covers more dictionary traditions than prior dictionary-digitisation studies, and provides unusually detailed prompts, per-dictionary analyses, code, and a gated [dataset release](https://huggingface.co/datasets/Davidsamuel101/MUDIDI). The Stage 1 evidence supports the central finding that recent general-purpose multimodal models are much stronger than the tested OCR/document baselines on this collection, although the markup comparison is not fully interface-fair.

In its present form, however, I would recommend major revision. The most serious issue is that the Stage 2 evaluator does not implement the metric described in the paper: extra predicted records can escape all three headline penalties. A synthetic prediction containing one correct record plus one wholly hallucinated record receives Entry Accuracy 1.0, MDF F1 1.0, and ReadOrderEdit 0.0. Stage 2 is also a same-page, transductive experiment: Pass 1 infers rules from the single page that Pass 2 is then scored on. Several manuscript claims and tables conflict with the current code or artifacts, including the OCR-hint numbers, Stage 2 model variant, number of dictionaries in the end-to-end analysis, annotation protocol, and claims that context or gold parse rules improve every condition. These problems are fixable, and correcting them would leave a potentially strong benchmark paper.

## Summary of Contributions

1. A dictionary-specific multilingual benchmark covering 30 bilingual or multilingual dictionaries, 17 language families, and approximately 20 scripts, with Stage 1 transcription gold and a ten-dictionary Stage 2 MDF subset.
2. A two-pass workflow separating faithful page transcription from dictionary-level structure induction and page-level MDF extraction.
3. A comparative evaluation of nine OCR/VLM/LLM systems using character, word, typography, reading-order, entry-matching, and MDF-field metrics.
4. Ablations of alphabet lists, preliminary OCR, dictionary introductions, MDF instructions, typography, gold parse rules, and predicted rather than gold Stage 1 input.
5. A practical dataset, codebase, prompts, error analysis, cost analysis, and discussion of CARE/FAIR/ICIP considerations.

## Claim–Evidence Assessment

| Main claim | Evidence in the paper and artifacts | Assessment |
|---|---|---|
| General-purpose multimodal models outperform the tested OCR and document-specialised systems in Stage 1. | Table 1 and the current Stage 1 reports show a large aggregate gap on TextEdit, GCER, and WER across the evaluated pages. | **Moderately strong.** The character-level result is persuasive for this sample, but uncertainty is absent and the markup comparison is partly determined by output interfaces. |
| Dictionary introductions and MDF instructions yield consistent Stage 2 gains. | Table 2 shows clear gains for Gemini, but mixed or negative effects for several Claude, GPT-5.5, and Qwen conditions. | **Weak as stated.** The effect is model- and context-dependent, not consistent. |
| Correcting Pass 1 parse rules fixes most remaining Stage 2 errors. | Table 23 reports a four-point macro improvement over five dictionaries, but Na falls from 0.97 to 0.94 and Tiri from 0.96 to 0.93. | **Weak.** Three of five cases improve; two become worse, and no correction-effort measurement is reported. |
| MUDIDI is the first highly multilingual dictionary-digitisation benchmark. | Existing document benchmarks are broader by document type, while prior dictionary work is narrower. | **Plausible only with a dictionary-specific qualifier.** “First multilingual benchmark” without that qualifier is too broad. |

## Strengths

### S1: Important and under-served task

The paper targets a genuine resource bottleneck: converting dense, historically varied dictionary pages into reusable structured lexical data. This is more demanding than plain OCR because it combines rare scripts, typography, layout, abbreviations, multilingual glosses, and dictionary-specific entry conventions. The motivation is technically and socially compelling, and the paper appropriately treats outputs as drafts requiring community or expert review rather than as authoritative replacements.

### S2: Useful two-stage decomposition and diagnostic design

Separating transcription from lexicographic interpretation is a good systems and evaluation choice. It makes failures more actionable: users can distinguish glyph recovery, reading order, typography, entry boundaries, and MDF-marker assignment. The Stage 1 combination of TextEdit, GCER, WER, markup F1, and reading-order error is substantially more informative than a single OCR score. The repository now also computes Stage 2 field-value, headword, and gloss GCER, which could further strengthen the paper if reported.

### S3: Broad dictionary and script coverage

Thirty dictionaries across many families and scripts is a meaningful advance over single-language or single-tradition dictionary-digitisation case studies. Inclusion of Cuneiform, Syriac, Arabic-based orthographies, Brahmic scripts, Han/Kana, Cyrillic, Greek, Hebrew, IPA, and Latin-based materials exposes failure modes hidden by English-centric OCR benchmarks. The per-dictionary appendix makes the heterogeneity visible rather than collapsing it entirely into one headline number.

### S4: Strong artifact ambition and practical analysis

The repository includes benchmark configurations, prompts, evaluation code, detailed reports, and per-language/script artifacts; the data is distributed through a gated Hugging Face benchmark with a DOI. The ablations are practitioner-oriented, and the cost, error, typography, and gold-rule analyses go beyond a minimal leaderboard. The ethical discussion of community control and colonial provenance is also welcome.

## Weaknesses

### W1: Stage 2 headline metrics do not penalise extra records correctly

This is the main technical issue. Section 3.5.2 describes entry matching and reading-order edit in terms of both gold and predicted entries, with unmatched predictions treated as insertions. The implementation instead defines “record accuracy” as record recall, `TP/(TP+FN)`, although it already records false positives ([`mdf_metrics.py`](src/mudidi/evaluation/stage2/mdf_metrics.py#L107), [`mdf_evaluator.py`](src/mudidi/evaluation/stage2/mdf_evaluator.py#L724)). Thus a system can add arbitrary extra records without lowering Entry Accuracy.

The same omission affects the other headline metrics:

- ReadOrderEdit is computed only from matched predicted records against the gold index sequence, so unmatched predicted records do not become insertions ([`mdf_evaluator.py`](src/mudidi/evaluation/stage2/mdf_evaluator.py#L53)).
- MDF field F1 counts missing and extra lines inside matched record pairs, but it does not add the lines of a wholly unmatched predicted record as field false positives ([`mdf_evaluator.py`](src/mudidi/evaluation/stage2/mdf_evaluator.py#L172)).
- Matching is greedy, content-based, and controlled by unvalidated thresholds of 0.6 for records and 0.7 for lines ([`mdf_align.py`](src/mudidi/evaluation/stage2/mdf_align.py#L48)). Near-perfect entry scores may therefore partly reflect alignment tolerance rather than exact boundary recovery.

A synthetic audit with one correct gold record and a prediction containing that record plus a hallucinated extra record produced: `Entry Accuracy=1.0`, `MDF F1=1.0`, and `ReadOrderEdit=0.0`, even though record precision was 0.5 and record F1 was 0.667. For the current Gemini + introduction + MDF run, replacing reported recall with record F1 changes 0.9940 to 0.9925, so this alone may not overturn the top-level record ranking. The effect on MDF F1 cannot be known without counting the fields in all unmatched predicted records and rerunning the evaluation.

**Required fix:** report record precision, recall, and F1; count every field in unmatched predicted records as a false positive and every field in unmatched gold records as a false negative; include unmatched records in reading-order insertion/deletion costs; add strict boundary or exact-headword segmentation metrics; validate alignment thresholds and compare greedy with optimal bipartite matching. Retain the present fuzzy metric only as a clearly labelled lenient diagnostic. Rerun all Stage 2 main and appendix tables after adding synthetic regression tests for extra, missing, duplicated, split, and merged records.

### W2: Stage 2 is a same-page, transductive evaluation and is too small for the generalisation claims

The paper describes Pass 1 as running once per dictionary on “one sample page” and Pass 2 as operating once per page, which suggests a reusable dictionary-level rule induction step. In the benchmark configuration, however, `one_page_per_entry` is enabled ([`stage2-e2e-full-sweep.yaml`](examples/configs/benchmark/stage2-e2e-full-sweep.yaml#L39)). The CLI first reduces the dictionary to its one gold-labelled Stage 2 page and only afterwards prepares Pass 1 samples ([`extract.py`](src/mudidi/cli/extract.py#L2035), [`extract.py`](src/mudidi/cli/extract.py#L2123)). With no explicit sample-page setting, Pass 1 uses the first remaining image ([`parse_rules_pages.py`](src/mudidi/utils/parse_rules_pages.py#L59)). Therefore Pass 1 sees the image and transcript of the exact page whose Pass 2 output is scored.

This is a valid two-pass, test-time adaptation setting, but it does not show that inferred dictionary rules generalise to unseen pages. With only one scored page per dictionary and ten dictionaries, the paper also cannot characterise within-dictionary layout variation or whole-volume performance.

**Required fix:** state clearly that the current result is transductive/same-page. More importantly, annotate at least two, preferably three, Stage 2 pages per dictionary: infer rules from page A and score B/C, or use leave-one-page-out evaluation. Report the current same-page result separately as an upper-bound test-time-adaptation condition.

### W3: Several manuscript claims conflict with the supplied artifacts

The current revision needs a single source-of-truth pass before publication:

- Section 3.4.1 says four models are used in Stage 2, while Section 3.4.3 says all Stage 2 experiments use Gemini 3.1 Pro. Table 2 clearly contains four models.
- Section 3.4.1 names the Qwen3-VL-235B Thinking model for Stage 2, but the benchmark configuration uses `qwen3-vl-235b-a22b-instruct` ([Stage 2 sweep](examples/configs/benchmark/stage2-e2e-full-sweep.yaml#L66)).
- The OCR-hint paragraph and appendix report baseline/hint TextEdit of 0.054/0.055 and markup F1 of 0.636/0.634. The current reports instead contain 0.042902/0.041370 and 0.7995/0.7836 ([baseline](evaluations/stage1_flat_per_lang_script_eval/gemini31pro_flat_alpha/stage1_flat_evaluation_report.json), [OCR hint](evaluations/stage1_flat_per_lang_script_eval/gemini31pro_flat_alpha_ocr/stage1_flat_evaluation_report.json)). The direction of the TextEdit effect changes.
- The end-to-end discussion says results are aggregated across all 30 dictionaries, but the archived end-to-end summary contains only the ten Stage 2 dictionaries ([summary](evaluations/archive/stage2_mdf_eval_e2e/stage2_mdf_eval_summary.csv)).
- The text says gold parse rules improve all five evaluated dictionaries, but Table 23 shows regressions for Na and Tiri.
- The conclusion says introductions and MDF instructions yield “consistent gains,” while Table 2 shows several negative interactions.
- The Stage 2 annotation section says two annotators label every one of ten dictionaries and reports Cohen’s kappa 0.99; the ethics section says gold for both stages was collected from one expert per source language.

These are more than copy-editing problems because they affect the identity of the evaluated systems, sample size, and interpretation of results. Regenerate all LaTeX tables and prose values directly from versioned evaluation summaries, and add a paper-validation script that fails on stale numbers.

### W4: Statistical support is insufficient for fine-grained rankings and ablation claims

Stage 1 has only three pages per dictionary, and Stage 2 has one page per dictionary. Each API model appears to have been run once per condition. There are no confidence intervals, paired tests, repeated decodes, or variance estimates, despite the paper itself observing that errors can change across repeated runs. Consequently, claims based on one- to five-point differences—especially the Stage 2 context ablations—are not statistically supported.

**Required fix:** use a dictionary-clustered bootstrap for Stage 1 and paired bootstrap or permutation intervals across dictionaries for Stage 2. Repeat API decoding at least three times for a representative subset or all ten Stage 2 pages, and report both between-page and between-run variance. Frame small differences as descriptive until intervals support them.

### W5: Baseline comparison and model provenance need tighter controls

The broad Stage 1 character-quality conclusion is useful, but the markup result is not an even capability comparison. General-purpose models are explicitly instructed to emit task-specific `<b>`/`<i>` tags, while several OCR systems expose plain text or their own formatting representation and consequently receive near-zero markup F1. This conflates recognition with compliance with the benchmark output protocol. Report tag-stripped text performance as the primary fair OCR comparison and evaluate markup only for systems given equivalent output adapters or prompts.

The paper should also provide exact model IDs, access dates, provider routing, image preprocessing/resolution, rendering DPI, reasoning setting, effective temperature, output-token caps, retry policy, and library versions. This matters because Gemini is called through a preview alias, while GPT, Claude, and Qwen are routed through OpenRouter. The official model pages identify the relevant endpoints—[Gemini 3.1 Pro Preview](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview), [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5), [Claude Opus 4.7](https://www.anthropic.com/news/claude-opus-4-7), and the [Qwen3-VL model card](https://huggingface.co/Qwen/Qwen3-VL-235B-A22B-Instruct)—but endpoint names alone do not pin a reproducible served snapshot. Likewise, “$0” for local/open-weight models should be labelled “no API fee”; compute, hardware, and energy costs are not zero.

### W6: Annotation, sampling, leakage, and release provenance are under-specified

The paper says three pages were randomly extracted per dictionary, but I could not locate a sampling script, sampling frame, random seed, or page-exclusion rules. The statement that “there was no data contamination” is also unsupported by a described audit. Since the source dictionaries are public-domain scans and could have appeared in model pretraining, the paper should distinguish source-document exposure from leakage of the held-out annotations and prompts.

The public dataset card currently reports 85 annotated Stage 1 pages, while the local dataset directory contains 86 and the current Stage 1 reports contain 90 evaluated page records. In the local copy, four report pages—two Georgian and two Yiddish—lack corresponding gold files. The public card also says Japanese has two annotated pages, whereas the local copy/report has three. The benchmark cannot presently be reproduced from the advertised public release without resolving these differences.

For the kappa claim, specify the annotation unit, label set, treatment of alignment and missing units, per-dictionary values, aggregation method, and whether annotators worked independently before adjudication. Release a de-identified agreement table and calculation script. The value 0.99 is otherwise not auditable and could be dominated by label prevalence.

## Methodology Assessment

| Criterion | Rating (1–5) | Assessment |
|---|:---:|---|
| **Soundness** | 2 | Stage 1 is broadly sensible, but the Stage 2 implementation contradicts the stated treatment of extra predictions and affects all headline Stage 2 metrics. |
| **Novelty** | 4 | The dictionary-specific breadth and MDF evaluation are genuinely novel and useful, even though multilingual document parsing benchmarks already exist. |
| **Reproducibility** | 2 | Code, prompts, data, and reports are available, but dataset counts, manuscript values, model variants, and current artifacts are not synchronised or pinned. |
| **Experimental design** | 2 | The model breadth and ablations are good; same-page Pass 1/Pass 2 evaluation, tiny Stage 2 coverage, and markup-interface asymmetry limit the conclusions. |
| **Statistical rigor** | 1 | No uncertainty estimates, significance tests, or repeated stochastic runs are reported. |
| **Scalability** | 3 | The two-stage workflow is practical and amortises rule induction, but unseen-page and full-volume scaling are not evaluated, and compute costs are incomplete. |

## Questions for the Authors

1. Was Pass 1 intentionally given the same page later scored in Pass 2? If so, how should readers distinguish this from dictionary-level few-shot rule induction that generalises to unseen pages?
2. What exactly was the unit and label space for Cohen’s kappa 0.99, how was it aggregated, and how does the two-annotator statement reconcile with the ethics section’s “one language expert per source language”?
3. Which Qwen model produced the Stage 2 results: the Instruct or Thinking checkpoint? What were the exact provider snapshots and run dates for every hosted model?
4. What contamination check was performed? Did it test exposure to the public source scans, the selected page images, the gold transcripts/MDF, or all three?
5. Which page set is canonical—90 in the evaluation reports, 86 in the supplied local dataset, or 85 in the public dataset card—and how can an external researcher reproduce every paper table from a pinned release?
6. How do Stage 2 results change after unmatched predicted-record fields are counted as false positives and record F1 replaces record recall?

## Minor Issues

- Correct “Ene-to-end pipeline” and “degradet” in the Stage 2 results discussion.
- Replace the appendix Stage 2 column label “OCR” with “MDF” or “MDF manual” where that is the actual ablation.
- Use “general-purpose multimodal models” rather than “general-purpose LLMs” for systems that receive images; reserve “document-specialised VLM” for the contrasting category.
- Use one consistent name for Syriac/Syrian and Bengali/Bengalese, while retaining historical book titles verbatim where necessary.
- “Authentic writing systems” is unclear; “historical and under-represented writing systems” would be more precise.
- Clarify whether Table 1’s TextEdit is a page macro-average while GCER/WER are corpus micro-averages; readers may otherwise compare unlike aggregation schemes.
- Report the total number of evaluated pages, entries, and MDF fields prominently in the abstract or main experimental setup rather than only in appendices.
- The practical recommendation that a second parse-rule run “will fix most errors” should be changed to a conditional statement supported by measured human effort and the mixed Table 23 results.

## Literature Positioning

The closest novelty is not multilingual document parsing in general, but *dictionary-specific* multilingual parsing into a lexicographic interchange structure. That distinction should be made throughout the introduction and conclusion.

[MDPBench](https://arxiv.org/abs/2603.28130), submitted in March 2026 before MUDIDI, contains 3,400 digital and photographed document images across 17 languages, explicitly studies non-Latin degradation, and uses public/private splits to mitigate leakage. It should be discussed because it weakens the broad statement that prior models had not been evaluated across diverse writing systems, while strengthening MUDIDI’s motivation through closely related findings. MUDIDI remains distinctive in its dictionary focus, within-page multi-script structure, typography, entry segmentation, and MDF assignment.

[MORE](https://arxiv.org/abs/2607.02956) covers 149 languages and complex real-world document structures. It postdates the original MUDIDI arXiv submission, so its absence is not a fault of the June version, but a current revision should acknowledge it and narrow “first highly multilingual” accordingly. [OmniDocBench](https://openaccess.thecvf.com/content/CVPR2025/html/Ouyang_OmniDocBench_Benchmarking_Diverse_PDF_Document_Parsing_with_Comprehensive_Annotations_CVPR_2025_paper.html) is appropriately used for alignment/read-order context, although MUDIDI should explain exactly where its Stage 2 metric departs from that benchmark. The cited historical-lexicography work is relevant, and the paper’s clearest advance over it is scale and controlled cross-model evaluation.

## Recommendations

**Overall assessment**: **Weak Reject in the present form; encourage major revision and resubmission.**

**Confidence**: **High** — I read the complete supplied manuscript, inspected the benchmark configurations and evaluation implementation, matched the headline tables to the current artifacts, and reproduced the central Stage 2 metric failure with a synthetic case. My uncertainty concerns how much corrected Stage 2 MDF F1 will change, which requires rerunning the evaluator.

**Contribution level**: **Moderate**, with the potential to become **Significant** after the metric, protocol, and release-provenance issues are corrected.

### Actionable Suggestions for Improvement

1. **Correct and validate Stage 2 evaluation first.** Add adversarial synthetic tests, penalise all unmatched content, report record P/R/F1 plus strict boundary metrics, perform threshold sensitivity analysis, and regenerate every Stage 2 result.
2. **Add an unseen-page Stage 2 experiment.** Infer parse rules from one page and score different pages from the same dictionary; keep same-page adaptation as a separately named condition.
3. **Create one versioned benchmark release.** Pin the paper to a Git commit, dataset revision/DOI version, exact page manifest, model/run manifest, and hashes of generated summary files. Make all tables generated from those summaries.
4. **Reconcile the manuscript.** Fix the four-model/Gemini-only contradiction, Qwen checkpoint, 10-versus-30 end-to-end scope, annotator count, stale OCR-hint values, and claims contradicted by Tables 2 and 23.
5. **Quantify uncertainty.** Use clustered or paired bootstrap intervals and repeated model runs, and avoid ranking models or prompt conditions whose intervals overlap substantially.
6. **Make comparisons interface-fair.** Separate plain transcription from task-specific markup generation, document image preprocessing for every backend, and report local compute cost rather than treating open weights as cost-free.
7. **Strengthen benchmark governance and provenance.** Publish the sampling procedure, IAA calculation, de-identified annotation summary, leakage analysis, canonical page count, and community/data-access rationale.

If these changes are made, the work would offer a distinctive and practically useful benchmark for multilingual computational lexicography rather than merely another document-OCR leaderboard.
