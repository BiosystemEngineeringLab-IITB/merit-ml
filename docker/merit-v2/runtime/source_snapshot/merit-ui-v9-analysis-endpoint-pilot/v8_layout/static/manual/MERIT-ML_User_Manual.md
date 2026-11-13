# MERIT-ML User Manual

**Evaluating ML readiness in tabular metabolomics datasets from Metabolomics Workbench**

**Updated 13 November 2026.** Use this guide to assess a public Metabolomics Workbench study, understand its results and adjust the settings for your classification task.

MERIT-ML means *Metabolomics Evaluation of Readiness and Interoperability of Tabular Data for Machine Learning*. Open [MERIT-ML](https://merit-ml.in/) to assess a public Metabolomics Workbench study, or use the [Docker application and source code](https://github.com/BiosystemEngineeringLab-IITB/merit-ml) on your own computer. MERIT-ML is independent of Metabolomics Workbench.

The guide covers every metric, the separate Structural and FAIR summaries, supporting information, all 13 numerical settings and eight custom alert cut-offs. It also explains class selection, sample and subject edits, guidance, downloads, bulk assessment and messages about computer or browser limits.

For technical details of retrieval, browser execution and local storage, see [How the web tool works](https://merit-ml.in/live/manual/#22-how-the-web-tool-works).

## 1. Start here

1. Enter a public Workbench study accession, for example `ST000001`, and start the assessment. A recent-study card can also fill the search and start that study.
2. Wait for input retrieval and the named calculation steps. **Generating report** follows the calculations; reaching the end of the calculation progress bar is not yet the finished report.
3. Read **Data at a glance** and the **Readiness profile** on Overview. Check each analysis and data file; the sources may disagree.
4. Define the classification task using the class editor on Overview or in **Label Structure and Class Support**. Select the number of cross-validation folds in **Show tools** and apply the change.
5. Review sample eligibility, repeated sample IDs, subject IDs, controls, analytical alerts and checks that could not be assessed. Change inputs only when you can justify the change from the deposit or your scientific question.
6. Inspect the metric tabs and save the assessment JSON, relevant feature lists and matrices. Preprocessing, feature selection and model fitting remain your responsibility.

MERIT-ML retrieves study data from Metabolomics Workbench when you request an assessment. The browser assesses one study at a time using your computer. Repository response time, network speed, matrix size and available memory affect how long it takes. For bulk assessment or matrices that exceed browser limits, use the local Docker application. You do not need to download a collection of studies before starting.

### 1.1 Search, availability and appearance

Use the study's `ST` accession, not a project, analysis (`AN`) or metabolite identifier. The application checks public availability. An invalid accession, an unavailable study, an explicitly identified embargo and a failed availability request are different states. A metadata-only report can be valid when no readable quantitative matrix is supplied through the supported endpoints.

**About**, **Publication**, **GitHub** and **User manual** provide scope, methods, source/installation instructions and this guide. The light/dark toggle changes display only. Searching for another study starts a separate assessment; it must not reuse the previous study's scientific results.

Recent studies are discovery aids, not a readiness ranking. If discovery cannot be loaded, use the accession search or the Workbench link. A raw-data file or an Excel attachment elsewhere in a study does not guarantee that one of the supported quantitative endpoints contains a readable matrix. MERIT-ML does not reconstruct feature tables from raw spectra or chromatograms.

### 1.2 Guided tours

A search-page tour and a report tour explain the interface separately. Use **Next**, **Back**, **Skip tour**, the close button or Escape. **Don't show tours automatically** opts out; **Take a tour** replays the relevant tour when you want it.

Tour state is saved for that browser and site, so opening another study or applying settings does not repeatedly launch it. Different browsers, private sessions, host ports and cleared site storage can have separate preferences. When persistent browser storage is blocked, session storage or the open page's memory is used instead. Tour preferences are not scientific inputs and are not sent as sample data. The tour can open existing tabs or details for explanation; it does not change classes, confirm subjects, alter parameters or submit an assessment.

## 2. Studies, analyses, data files and denominators

### 2.1 What one result represents

A study can contain several analyses. Each analysis can contain independently readable **datatable**, **mwTab** and **untarg_data** matrices. A **matrix** in this guide means one analysis × endpoint. The readiness profile never averages endpoints or analyses into one study score.

| Term | Meaning |
|---|---|
| datatable | Quantitative table obtained from the official `/datatable/` endpoint, not `/datatable/file/`. |
| mwTab | A readable quantitative metabolite-data block in the analysis's mwTab record. The record can contain metadata without a valid numeric matrix. |
| untarg_data | A readable untargeted feature table from the official untarg_data endpoint. An entry in the repository’s untargeted-study list does not by itself establish matrix validity. |
| Tier 1 | The grouping of datatable and mwTab used in source descriptions. They remain separate sources and can differ in samples, features and values. |
| Tier 2 | The grouping used for untarg_data in source descriptions. The tier is a source convention, not an analytical-quality ranking. |
| Position | One sample row of datatable or untarg_data, or one sample column of an mwTab quantitative block. |
| Distinct sample ID | A sample identifier counted once even when it occurs at several positions. It does not necessarily identify an independent subject. |
| ML-eligible position | A measured position remaining after the current eligibility rules and edits, excluding Tier 1 alignment rows and controls not selected for modelling. |
| Selected-class sample | An ML-eligible sample ID assigned to one of the classes currently selected. Not every eligible sample has to belong to those classes. |

Different metrics use different denominators deliberately. Cell and sample-median diagnostics generally use **ML-eligible positions**; class support and separability use **distinct sample IDs in the classes in force**. A feature denominator can be all feature positions, judged features, evaluated non-low-information features, eligible feature labels or named features. These are specified for each metric below. Do not interchange them.

### 2.2 Tier 1 sample alignment

A datatable can contain an additional all-zero sample row whose sample ID is absent from the paired mwTab quantitative block. MERIT-ML identifies the specific Tier 1 alignment condition, reports the affected counts and IDs, and excludes those positions from analytical and class calculations. It does not establish from this alone whether a physical sample was ever experimentally measured.

An alignment position cannot be made ML-eligible by an edit. The same sample ID may nevertheless be measured in another analysis; the sample editor's per-matrix status strip shows that difference. Do not delete every all-zero row indiscriminately: an all-zero row with a measured counterpart is a different case.

### 2.3 Controls and missing identifiers

Controls are recognised as **QC-like**, **blank-like** or **standard-like**, not experimentally verified. Default recognition considers sample IDs and factor values, sample-table evidence and the distributed curated corrections. Details identify the keyword or correction and where it came from. Unrecognised controls can remain eligible until you review them.

Empty factor fields are distinct from deposited words such as `NA`, `None` or `Unknown`. Those words can be genuine levels, for example an untreated control, so class definitions do not automatically delete them. Subject-placeholder handling treats empty, `-`, NA, N/A, NaN, null, none, unknown and not applicable (case-insensitive) as no subject. Numeric-cell missing-token handling has its own rules; a string's meaning is not assumed to be identical in every context.

## 3. Read the Overview

### 3.1 Study information and data-source availability

The report begins **MERIT-ML Assessment Report — ST…** and identifies the study and its available analyses, quantitative endpoints, repository metadata and citation. Disease, organism, sample source and other descriptive fields are repository evidence, not inferred prediction targets. For example, the disease display uses the dedicated Workbench disease evidence; a displayed disease does not automatically define the selected classes.

The data-source information distinguishes present valid matrices, records without a readable matrix, unavailable endpoints and failures. In the browser, a resource refusal is shown separately from absence. If one endpoint cannot be assessed, other available endpoints and analyses can still produce results. Follow the named endpoint and omission reason rather than interpreting a blank as absence or zero.

### 3.2 Data at a glance

This collapsible block gives a deterministic plain-language explanation, not a generated diagnosis or a new score. It describes mwTab when available (otherwise datatable), and untarg_data separately. The full profile below still includes every assessed endpoint.

Its analysis cards explain, where applicable:

- measured positions, distinct ML-eligible sample IDs, controls, Tier 1 alignment and positions set aside by edits;
- feature counts and the annotation classes actually found, such as named metabolites, m/z–RT features or NMR bins;
- Structural items and explicit reasons for unavailable items;
- the active classes, class sizes, selected samples, count requirement, class support, separability and its chance level;
- missing and zero categories, low-information features, redundancy and sample-median outliers, with the supporting burdens and active settings;
- QC reproducibility, unit/scale cautions, subject sharing, batch-like evidence and important restrictions where available;
- task-dependent feature-association results, actual test names or the reason no test ran;
- practical review guidance and source-matrix downloads.

The narrative follows **applied** settings, classes and edits. A control you have changed but not applied is not yet the active assessment. No eligible samples, no samples in selected classes, unavailable checks and calculations that could not be recomputed are stated explicitly. The text must not be read as four passing checks when only one was assessed.

### 3.3 Derived context

**Study-design context** summarises evidence for cohort/subject-based, experimental/replicate-based or unclear design. It is a MERIT-derived interpretation, not a native repository field or a readiness gate. Confidence and source evidence help interpret small sample counts; a cell-culture experiment need not have the same aim as a population classifier.

**Analyzer capability and acquisition evidence** separate instrument-derived HRMS-capable, nominal/unit-resolution and indeterminate classifications from acquisition wording. Analyzer capability does not prove the resolving power achieved, targeted/untargeted status, annotation quality or experimental quality. Targeted/untargeted-like wording, scan-mode terms, an untargeted-file marker and analysis-linked MS2 evidence are independent evidence streams, with their scope and provenance. Evidence in a study-wide section need not describe each analysis. Historical contextual reference evidence, where used, is labelled separately from fresh programmatic evidence.

### 3.4 Readiness-profile columns

| Column | What to read |
|---|---|
| Analysis | The AN accession; keep different analyses separate. |
| Data file | datatable, mwTab or untarg_data. Grouped endpoints have the same summary signature; their numeric values can still differ. |
| Summary | Count-requirement status, strongest analytical alert and assessed coverage, with relevant subject/structural notes. It summarises count support and analytical alerts without combining them into one score. |
| Cross-validation sample-count support | Smallest class, required folds, class/sample counts, p/n, class support, separability/chance and subject warnings. |
| Analytical alerts · fixed reference | The named checks raising low/extreme alerts, their values/cut-offs, checks unavailable and units documentation. |
| Selected annotation and metadata indicators | Named-feature coverage, named features mapped to RefMet, external IDs, mass/RT-field presence and LC-method context. These do not set the alert/count summary. |

**Count requirement met** means at least two classes and at least `k` distinct labelled eligible sample IDs in the smallest class. **Count requirement not met** includes one class or an insufficient smallest class. **Cannot assess class counts** includes no labelled eligible samples or an absent selected class in that particular matrix. An unreadable or missing matrix is a separate availability state.

**No alerts** means no assessed check crosses the current low/extreme boundary. **Low** means at least one low alert and no extreme alert. **Extreme** means at least one extreme alert, potentially with additional low alerts. The numbers of each are shown. **Not assessed** names a missing calculation, not a successful check.

Read `4/4 checks assessed` independently of the alert label. With fewer than four assessed, a no-alert result is incomplete evidence. All four can be assessed and all four can raise alerts; the summary uses the strongest tier and reports the counts, rather than an average. Units declared is outside those four.

“Same summary” does not mean byte-identical matrices or identical annotation, feature counts or p/n. Differences are retained within the grouped row or in metric tabs. No endpoint is selected as a universal winner.

### 3.5 Definitions, tiles, colour and supporting evidence

Expand the readiness description and cut-off panel for the active reference, settings and absolute burdens. The **Scores · every analysis × endpoint × item** grid preserves individual values. Use **Show numbers**, the legend, hover/focus/tap help and section links; colours alone are insufficient. Hatched/unavailable/no-matrix states differ from numerical zero.

Individual metric headings explain what a higher value means. A high annotation score means more name/mapping coverage; it is not proof that classes can be predicted. A small RSD is generally preferable for reproducibility even though RSD is informational. Units, instrument categories and numerical scale must be interpreted in their own context. The Structural mean and FAIR documentation index are separate diagnostic summaries; neither contributes to the readiness-profile decision.

## 4. Choose the classification task

### 4.1 Whole strings, factors and observed combinations

By default each distinct complete `SUBJECT_SAMPLE_FACTORS` string is a class. For a synthetic label `Treatment: control | Time: 4 h | Sex: female`, the whole three-factor combination is one default class. Selecting Treatment alone pools every selected sample with the same Treatment value across Time and Sex; it does not hold those factors constant.

MERIT splits a label conservatively at the spaced delimiter ` | `: every segment must have a unique non-empty factor name followed by `:`. It does not split an embedded unspaced pipe inside a value. Unreadable, ambiguous or conflicting labels are reported rather than silently assigned to an invented class.

The selector uses the study-wide available factors and observed value combinations, assembled across its matrices. A factor needs at least two values to be selectable. Choose one or several factors and at least two **observed** classes. A class can be a single value or a joint combination; the selector does not generate a Cartesian product of combinations that never occurred. Selecting several values does not merge them into one class.

Study-wide option counts are discovery counts. They do not guarantee that a combination is measured, eligible or present in every matrix. After **Apply classes**, each matrix is counted from its own eligible sample IDs. A factor that varies in the study can be constant in one matrix; a chosen class absent in that matrix makes its class-dependent assessment unavailable. A factor may disappear or a class choice may reset after edits; the report states a choice that could not be applied.

Use **Use the whole factor string as deposited** to restore default classes. Overview and Label Structure share the same class definition. Selecting another scientifically meaningful task can change count support, class support, separability, feature associations and task-specific recommendations; it does not create samples or improve the original measurements.

### 4.2 Samples left out and denominator checks

The tab reports the class definition, every class count, smallest/largest class and samples outside the chosen task. Reasons include no label, conflicting labels, another value/combination, an unsplittable string or a missing selected factor. Invalid study-wide choices fall back with an explanation; a valid study-wide choice with a zero-sized class in one matrix is not silently reduced to fewer classes there.

Repeated sample IDs count once for class support. Contradictory sample-table labels for one sample ID leave that ID unassigned unless corrected. Empty labels are separate from an explicit word such as `NA`. Choosing a label is a scientific decision; do not select a task only to obtain a met condition or stronger separability.

### 4.3 Other factors across the chosen classes

This informational block appears for a selected-factor task. Each remaining deposited factor is compared on the selected sample IDs. The first applicable state is reported:

| State | Exact interpretation |
|---|---|
| Not assessable | Some selected samples do not report the factor; coverage is shown. |
| One reported level | Every selected sample has the same reported value. |
| One value per sample | Every value belongs to just one selected sample, so levels are not shared. |
| Values absent from some classes | At least one observed value occurs in one selected class but has zero observations in another. This is an observed absence, not a missing-field count. |
| Same proportions | Every value has exactly the same share in every selected class, compared using counts rather than rounded percentages. |
| Different proportions | Every value occurs in every class, but shares differ. The largest gap is shown in percentage points. |

Qualifying remaining factors are also compared jointly when at least two are varying, fully reported and shared between samples. Details identify the factors and counts; value sets above 20 levels can omit the small hover count table. There is no imbalance score or statistical significance threshold. Equal observed proportions do not establish freedom from confounding: undeclared factors, subjects, batch, run order and interactions outside the recorded factors can remain important.

## 5. Adjust samples, labels, batches and subjects

Open **Adjust Matrix Properties**. There is one row per sample ID of the study; an edit applies wherever that ID occurs, not just to one selected endpoint. Matrix-status strips show which analyses/endpoints contain it, whether it occupies several positions, its measured/alignment/control/eligible status and which statuses changed.

| Control | Effect after Apply sample edits |
|---|---|
| Use as: as deposited | Restore default eligibility for that ID. |
| Use as: ML-eligible | Include measured positions for modelling where allowed. Tier 1 alignment positions remain ineligible. |
| Use as: not ML-eligible | Set aside the measured sample without claiming it is a QC or blank. |
| Use as: leave out | Exclude the sample by an explicit edit; this is reported separately from controls. |
| Factor label | Replace the label used for that sample; blank edit fields retain the deposited/default label. |
| Batch-like label | Override the label used by Sample annotations; `-` gives it no batch-like label. This is informational. |
| Subject | Override the deposited subject; `-` gives it no subject. Blank retains the deposited value. |
| Subject confirmation | Confirm that the subject values identify real persons, animals or sources, not group names or placeholders. This enables supported subject-aware feature tests. |

The **Show** filters select visible statuses; grouping only reorganises the table. The bulk **set all shown rows to…** control affects rows currently visible, then requires Apply. Subject shortcuts set every listed row to its own subject, no subject or the deposited subjects; Apply is still required. Inspect their scope before using them. Assigning one subject per sample is an edit, not evidence that those samples are independent.

**Deposited evidence** shows sample/subject IDs, factor values, the keyword and source behind control status or a curated decision, and additional sample data. A generic shared subject such as a species or “Human” can be an unsuitable subject identifier. Confirm only after checking what the value means. Missing subject IDs do not establish independence.

**Apply sample edits** recalculates applicable panels and downloads; **Clear all edits** restores deposited/default inputs. None of these edits changes Workbench. Relabelling or confirming a subject alone must not restrict label-independent analytical checks to the chosen classes. Eligibility changes alter their sample denominator. A reported inability to recompute must be read before treating a displayed value as an updated result.

Confirmed subjects change the informational feature-association test, not class separability, the class-count condition or the four analytical scores. The count condition counts sample IDs, not independent subjects. Grouped train/test or cross-validation splits remain a downstream responsibility; MERIT-ML does not construct or validate those folds.

## 6. Structural diagnostics

Structural has **four scored items** and a separate diagnostic mean. It does not set analytical alerts or the count-support result. Values below 100 identify the relevant issue; 100 does not rule out all structural problems or subject dependence.

### 6.1 Sample support

Score = `100 × min(1, n / N)`, with `n` distinct ML-eligible sample IDs and `N = 20` by default. N can be 5–100 in steps of 1. With no eligible sample it is not assessed. The target is a descriptive working reference, not a power calculation, per-class threshold or readiness gate. Changing N affects this item and the Structural mean, not the k-fold count requirement.

### 6.2 Samples with a factor label

Score = `100 × eligible positions with a non-empty factor label / eligible positions`. A label is non-empty when at least one value is filled. Only an empty value or `-` is empty for this check; deposited words such as `None` and `NA` remain values. A label can describe batch, age or an irrelevant factor and still count. It need not be an appropriate prediction target or have every constituent factor filled. No eligible positions means not assessed.

### 6.3 Numeric value cells

Score = `100 × numeric cells / (numeric cells + text cells)` over eligible positions. Recognised missing cells are excluded from this denominator. Empty cells and missing tokens such as NA, NaN, null, `-` and `.` are missing; detection-limit entries, spreadsheet errors and malformed numbers can count as text. The parser recognises the supported malformed-exponent notation as numeric where specified. With no numeric or text cell remaining, the item is not assessed. This differs from the missing-entry rate, which counts all empty/non-numeric cells against all eligible value cells.

### 6.4 Duplicate sample IDs

The positive score is `100 × distinct eligible sample IDs / eligible positions`; 100 means no repeated ID among those positions. The tab is headed **Duplicate sample IDs**, while some tables describe the positive quantity as distinct sample IDs per position. IDs are not automatically treated as independent samples or as known technical replicates. Repeated measurements should be combined appropriately or kept on one side of a train/test split.

### 6.5 Structural mean and informational evidence

The displayed Structural mean averages only the assessable items. No assessable items means no mean, not zero. The number assessed matters. High means do not cancel specific defects.

Details retain deposited/measured/eligible positions and distinct-ID counts; repeated positions and IDs; feature/value-cell counts; missing/text/numeric counts and text classes; completeness of all factor values; factor-label provenance; sample-table membership and sample-table line counts; declared non-control samples represented in the matrix; subject coverage, distinct/shared subjects and dependence warnings; Tier 1 alignment; and agreement of sample identities/position counts and feature counts across endpoints. Endpoints can share IDs yet have different numbers of positions, one can be a subset, they can partly overlap, or they can have none in common. A count difference alone does not identify the cause; inspect the IDs and source evidence.

## 7. Metadata and FAIR Reusability

This section has **five scored components**, assessed per analysis except study metadata, which is shared. It reports a separate documentation index, not a modelling certificate or formal comprehensive FAIR certification. Higher values mean more of the specified documentation or linkage is present.

### 7.1 Study metadata

Seven yes/no checks cover a DOI, a linked publication, funding, contributors, declared project/study type, a project or study summary of at least **20 words**, and documented raw data. Score = `100 × checks passed / 7`. Raw-data evidence can come from the study page or a data-format/raw-file declaration; it is documentation, not verification that the raw files are complete or reprocessable. Expand each item for the deposited evidence and source.

### 7.2 RefMet mapping of deposited metabolite records

Score = `100 × records with a valid deposited RefMet name / metabolite records` for the analysis. Records present but no valid mapping yield zero; no records means not assessed. Blank, `-`, Unknown, Standard and Protein placeholders are not valid RefMet names. This is a **record-level** component: duplicates remain records. MERIT-ML uses Workbench's assigned mapping; it does not independently identify a metabolite.

Do not confuse this with the Annotation tab's mapping of **named matrix features**, which has another denominator and can differ by endpoint.

### 7.3 External database cross-references via RefMet

Score = the percentage of valid RefMet-mapped records whose RefMet term has at least one cross-reference in the implemented ChEBI, InChIKey, PubChem, HMDB, KEGG or LIPID MAPS fields. No valid RefMet records means not assessed. These are links supplied through RefMet, not necessarily identifiers deposited by the study. Having one does not prove that the experimental annotation or structure assignment is correct. A mapped lipid/class term can lack a structure-level identifier.

### 7.4 Mass/RT-like metadata presence

This binary component is 100 when the mwTab annotation evidence contains a filled mass/m/z-like or retention-time/index-like field under the field-recognition rules described here, otherwise zero. An mwTab record without an annotation table scores zero; a record that cannot be read is distinguished from absent coordinate content.

The scored evidence concerns annotation metadata for named metabolites. The m/z–RT text of an unnamed feature or an untargeted results-file header is displayed as contextual evidence and is not silently counted as this annotation field. Presence of exact/neutral mass is not the same as a measured m/z, and retention index is not retention time.

Coordinate details show field provenance and coverage, mass-like versus strict m/z-like evidence, RT/RI evidence, coverage of either/both coordinates, RT-unit category and adduct declarations where present. These explain the component; they are not extra scored metrics or proof of identification confidence.

### 7.5 Basic LC-method context

Only analyses recognised as explicitly LC from the declared chromatography type are assessed. Score = one third each for a column declaration, **both** mobile phases and a gradient/composition declaration. The gradient rule requires percentage/composition wording with time information or an isocratic declaration. Dedicated chromatography fields are read; an external method file or unstructured narrative is not assumed to supply missing components.

Flow rate, temperature, dimensions, particle size and basic/extended/full-context details are informational. GC or another explicit non-LC type is not applicable. An unknown/unrecognised type is shown with the actual declaration or “LC not explicitly identified”, rather than proof of no LC. LC context does not demonstrate comparable retention times across studies.

### 7.6 FAIR documentation index

The index is the arithmetic mean of **assessable** FAIR components, so different analyses can have different numbers of components in the denominator. Unavailable components are not automatically zero; a measured absence in an assessable binary component is zero. Inspect the component values and number assessed. The index never offsets analytical alerts, changes count support or contributes to a universal readiness score. Deposited values, lookup provenance, coordinate details and LC components remain available alongside it.

## 8. Analytical QC: samples, controls and processing

Analytical QC contains **five scored items**: Missing entries and zeros, Low-information features, Redundant features, Outlier samples and Units declared. Only the first four can raise analytical review alerts. The remaining blocks below are informational, including QC reproducibility.

### 8.1 Sample eligibility and analytical controls

The panel counts measured positions, eligible positions, QC-like, blank-like and standard-like positions, Tier 1 alignment, and edited exclusions/set-aside positions per matrix. It also distinguishes controls declared in the sample table from controls actually measured in this endpoint, including **Not measured here**. Sample-table counts and measured-position counts are different quantities.

The default keyword rule searches sample IDs and **factor values**, not factor names. A keyword in the sample ID decides before a keyword in factor values; blank matches take precedence over QC matches. The curated correction can override a keyword or supply a documented additional QC, blank or standard. Every position with that sample ID follows its per-analysis identity rule; a sample can have a different alignment/measurement status in another matrix. Details give keyword, location, source label and curated evidence. The QC keyword set is `qc`, `nist`, `ltr`, `sst`, `pool`, `reference`, `quality control`, `pooled qc`, `system suitability`, `calibration standard` and `drift`. The blank set is `blank`, `solvent`, `process blank`, `method blank` and `reagent blank`. Matching is case-insensitive; qc/nist/ltr/sst do not match immediately after a letter. Names alone do not verify material or injection type. Full lists are downloadable; a short on-page example list need not display every ID.

### 8.2 QC reproducibility: median QC RSD

This is **informational**, with lower RSD indicating less relative spread in repeated measurements. It does not raise an alert and is not averaged into a quality score.

QC-like positions are grouped by deposited material label, or by sample ID when that label is empty. The **largest group** is used, requiring at least **three injections** of one QC material. Blank-like and standard-like positions are not substituted. Units must indicate a supported linear family—concentration/amount, intensity/area/counts, or normalised/relative/ratio—and negative values must be absent. Log-indicated, other/mixed or undeclared units, insufficient injections or unavailable values produce an explicit not-assessed reason.

For each feature, detected means a finite **non-zero** value. Detection percentage = detected injections / injections × 100. RSD = `100 × sample SD / mean` over the detected values, with SD using `ddof = 1` and at least two detected values. The headline median RSD uses only features detected in **more than 70%** of injections.

A passing feature has detection >70% **and** RSD strictly below the selected cut-off, default **20%**, adjustable **10–40%** in steps of 1. Exactly 70% detection or an RSD equal to the cut-off does not pass. Changing the RSD cut-off changes passing/failing counts, not the headline median calculation. Details/downloads give material, injections, detected coverage, per-feature RSD and detection/RSD failure counts. Repeated IDs alone do not establish that they are the appropriate QC replicates.

### 8.3 Processing evidence

Read the quoted dedicated software-version, processing-parameter-file, analysis-detail and NMR-processing fields, software mentions and minFraction/minfrac wording. Details show named processing steps, where the words were found and record-identity warnings. SAMPLEPREP:PROCESSING_METHOD is not treated as computational processing evidence. A software mention is not proof it produced this matrix; a named parameter file is not parsed as though its contents were available. These are deposited evidence, not a verified preprocessing pipeline or normalisation score.

## 9. Analytical QC: the four alerted metrics

The four scores describe different properties and denominators. Their default R1 alert boundaries are given in section 15. Their settings change metric calculations; alert percentiles/custom cut-offs change how those values are flagged. Classes selected for a prediction task do **not** filter the samples used by these four label-independent checks.

### 9.1 Missing entries and zeros

The denominator is **all feature positions × ML-eligible sample positions**, including unnamed features. Each category's score is `100 × (1 − category cells / eligible cells)`. The displayed metric is the **lowest applicable category score**, equivalently 100 minus the largest category percentage. It is not 100 minus the sum of categories and is not an average of per-sample completeness scores.

| Category | Meaning |
|---|---|
| I1: Missing entries | Empty or non-numeric cells, with the six entry classes reported separately. Assessed for all three data sources, including datatable. |
| I2: Zeros, meaning unknown | Every mwTab/untarg_data zero. For datatable: zeros also zero in mwTab, not traceable to a paired cell, or in an unpaired datatable. |
| I3: Zeros hiding a missing value | A paired datatable zero whose matching mwTab cell has no number: empty, missing token or text. |
| I4: Zeros replacing a value | A paired datatable zero whose matching mwTab cell has a non-zero number, including negative/positive values and values that round to zero at datatable precision. |

I3 and I4 apply only when paired evidence exists. All categories use the same eligible-cell denominator. A zero is not automatically called missing or incorrect. The metric reports prevalence and provenance; it does not change the deposited values.

Datatable zeros are traced by sample ID and **normalised feature name**, not row/column order. Feature normalisation trims, lowercases, collapses spaces, reads commas as underscores and apostrophes as carets. For repeated feature names or sample IDs, all candidate paired cells are considered and a category is assigned only when they agree; unresolved candidates remain untraced. Details identify extensions using repeated IDs/names. This differs from analyses restricted to unambiguous one-to-one cells.

The six missing/non-numeric classes are empty, missing-value token, detection-limit entry, non-finite number, malformed number and other text. Details include whole-deposited-matrix rates, eligible rates, per-sample distributions, samples without numeric values/all zeros, zero provenance and negative values written as zero. Whole-matrix figures include a different denominator from the score. At zero eligible samples or zero features, the metric is not assessed.

Higher = a smaller largest category. A score of 100 means no cell falls into a scored category, not verified absence of every analytical problem. A negative value written as zero has its own provenance flag; numerical rounding and clipping cannot always be distinguished from the datatable alone. For modelling, review source-specific missingness and do not uniformly turn all zeros into missing values.

### 9.2 Low-information features

The input is numeric feature values on eligible **positions**; invalid/missing entries are omitted and zeros are real numeric values. A feature is **judged** with at least three numeric values. Constant means one distinct numeric value. For non-constant features, near-zero variance means both:

- count of most common numeric value / count of second most common value **> R**, default 19;
- `100 × distinct numeric values / eligible positions ≤ U`, default 10%.

R can be 5–99 in steps of 1; U can be 1–25% in steps of 1. Exactly R does not meet the frequency rule; exactly U does meet the uniqueness rule. Missing positions remain in the uniqueness denominator. The score is `100 × (1 − (constant + near-zero-variance features) / judged features)`. Each feature is counted once. Fewer than five eligible positions or no judged feature makes it not assessed. Features with too few values are reported rather than assumed informative.

Higher = fewer judged features flagged by this rule. **Examples** shows up to five flagged features, constants first. Details distinguish constant, near-zero variance and unjudged features, with observations and active settings. The full JSON list is generated on request for every relevant analysis × endpoint, with names/reasons and settings, rather than embedded as every feature list in the report.

### 9.3 Relative-spread caution (CV)

This belongs beside Low-information features but is **not scored**. Among features not already flagged, CV = `100 × sample SD / mean`, with at least three numeric values, positive mean and SD `ddof = 1`. It is considered only for declared concentration/amount or intensity/area/count units with no negative values. It is not interpreted on log-indicated, normalised, other/mixed or undeclared units.

The caution counts features with CV **below** C, default 1%, adjustable 0.1–5% in steps of 0.1. These can be internal standards, rounded/fixed quantities or genuinely stable metabolites. The value is not proof of a defect. Changing C does not change the low-information score or analytical alert; the full low-information download also includes the caution features.

### 9.4 Redundant features

Features must have at least three values and not be flagged by the current low-information rule. Each pair requires at least **10 shared numeric positions**. Missing entries are omitted; zeros remain values. The metric needs at least **10 eligible positions** and at least two evaluated features, otherwise it is not assessed.

Each feature is ranked once over its observed values, using average ranks for ties. It correlates those ranks on the pair's shared observations. With complete data this is usual Spearman correlation; with missing data it need not equal separately re-ranking every pair's shared subset. Absolute correlation is used, so strong negative and positive relationships can both count.

The threshold c defaults to **0.90**, adjustable **0.80–0.99** in steps of 0.01. A pair must exceed c; values within `1e−5` of c are treated as equal, to avoid numerical rounding artefacts.

The **fast caret-style selection** flags, for each exceeding pair, the member with the larger mean absolute correlation across the evaluated features; its documented tie rule resolves equal means. A feature selected by multiple pairs is counted once. Mean correlations are not iteratively recalculated after each removal. Score = `100 × (1 − flagged features / evaluated features)`. Removing the flagged set leaves no evaluated pair above the threshold, but the set is not guaranteed to be the smallest possible removal set.

Higher = fewer features selected for removal by this rule. The count of correlated **pairs**, count of **flagged features**, and largest **connected group** are different. Connected does not mean every member is correlated above the threshold with every other member. Examples show up to five strongest pairs; downloads include flagged features with strongest partners and the exceeding pairs for all analyses/endpoints. A small-sample caution is shown at 10–19 eligible samples; this does not alter the score. Changing low-information settings can change which features are evaluated for redundancy. Changing labels/subject confirmation alone should not recalculate a different label-independent result.

Redundancy is method-dependent: correlated ions, isotopes or biochemical signals need not prevent predictive modelling. Review dimensionality, model choice, interpretation and feature selection rather than treating an alert as a universal exclusion.

### 9.5 Outlier samples: sample-median outliers

For each eligible position, take the median of its numeric feature values, **including zeros**. A position with no numeric median is excluded from this diagnostic's denominator and counted separately. At least five positions with a median are required.

Let Q1 and Q3 be the quartiles of the position medians and IQR = Q3 − Q1. A position is flagged when its median is **below** `Q1 − a × IQR` or **above** `Q3 + a × IQR`. The multiplier a is **1.5** by default, adjustable **1.0–3.0** in steps of 0.1. Values equal to a fence are not outliers. With IQR = 0 the fences collapse; the report states this rather than assuming no outlier is possible.

Score = `100 × (1 − flagged positions / positions with a numeric median)`. Higher = fewer medians outside the selected fences. Details give evaluated positions, missing medians, quartiles, IQR, fences, IDs, medians, side of fence and examples; a full list is downloadable. A repeated ID can occupy several tested positions, so an ID alone is not necessarily one unique observation. The matrix export's position field helps locate the deposited row/column.

The test concerns **sample medians only**. A biologically unusual multivariate feature profile can pass; zeros or sample-amount differences can change medians. Do not automatically discard every flagged sample or interpret 100 as no possible outlier.

## 10. Analytical QC: scale, units and contextual evidence

### 10.1 Units declared

This is Analytical QC's fifth scored item: **100** for a valid declaration and **0** otherwise. It is a documentation note, not one of the four alerts. A valid declaration is non-empty, non-placeholder text; NA, not applicable, `-` or a number alone do not supply usable units.

For mwTab/datatable, the quantitative data-block UNITS declaration is used for that analysis. Datatable shares this declaration; it is not independently verified by datatable numbers. For untarg_data, the declaration associated with the mwTab results-file line is considered separately. A data-block declaration is not automatically assigned to untarg_data. Missing declaration evidence is not proof that measurements have no physical units.

### 10.2 Deposited measurement scale and unit family

Scale descriptors are informational: the median and quartiles of non-zero numeric values, including negatives; negative-value counts/share; positive-value 5th–95th percentiles and orders of magnitude; and declared unit family. Values are not transformed, converted into common units or compared as if every endpoint used the same measurement scale.

Families include concentration/amount, intensity/area/counts, normalised/relative/ratio, log indicated and other/mixed, plus no declaration. The family comes from wording and rules, not a guarantee of the preprocessing history. It controls whether the optional CV/RSD calculations are meaningful under their implemented assumptions.

For eligible samples with positive medians, the panel can show their median, the 5th–95th percentile spread in orders of magnitude and samples more than tenfold from the typical sample median. Negative/zero medians are not silently put on a logarithmic axis. Paired datatable evidence can show whether non-zero mwTab numbers are retained to deposited precision. None of these descriptors has a universally better high or low value.

### 10.3 Platform/acquisition and per-analysis data summary

The platform/acquisition block reports analytical platform, analyzer capability, relevant wording, scan modes, repository/file markers and linked MS2 evidence with source attribution. The same evidence can also appear under Derived context on Overview. It is informational and does not verify targeted/untargeted acquisition or annotation confidence.

**Per-Analysis Data Summary** brings together available matrices, features, measured/eligible samples, controls, feature-to-sample ratio, annotation and numerical context for each analysis/endpoint. These quantities keep their original denominators; they are not an additional score. Data availability, context and documentation can be useful even when class counts or an analytical check are unavailable.

### 10.4 Sample annotations: MetaBatch-style filters

This informational analysis reads technical annotations from factor records, additional sample metadata and the batch-like edit. It recognises batch, plate, run order and run/day-type fields by names and, for batch/plate/run-like labels, their values. A candidate must have at least two distinct values, non-missing values for **at least 60%** of eligible positions, and distinct values no more than **90%** of positions. These filters are fixed, not Show tools sliders.

Details state the field, provenance, coverage, distinct values, whether it passes and why it fails. IDs, control-related fields and constant/nearly unique fields are not automatically suitable batch labels. A reported factor association can flag that each batch level contains one factor value, including a one-to-one or more general nesting relationship. This is evidence of a possible confounded design, not a fitted correction, a batch-effect test or proof of absence of batch effects when no field is found. It does not enter the readiness profile or FAIR index.

### 10.5 Additional sample data

Other sample-table fields are shown with field name, number of sample-table **records** reporting a value, distinct-value count and examples. Repeated sample-table lines count as records, not independent samples. The source can be mwTab text or its JSON fallback and is identified.

Fields already represented as raw-file evidence, factors, identical factor values, sample IDs, subject IDs or technical sample annotations are not repeated; exclusions and reasons are available. A field name alone does not establish a phenotype, diagnosis or verified covariate.

## 11. Annotation / Interoperability

There are **two scored items**, with name/mapping/class/repetition evidence. They describe interpretation and reuse; unnamed features can still support modelling and naming alone does not establish correct metabolite identification.

### 11.1 Named-feature coverage

Score = `100 × named matrix features / eligible feature labels`. Feature labels are processed by the priority-ordered classifier. Named features include supported named/name-resolved categories. Features without a reported metabolite name include unresolved identifiers, class-level/tentative labels, formulae, m/z–RT, m/z-only, NMR bins, generic feature IDs and unknown labels. Empty/artifact/sample-metadata/protein labels and recognised malformed matrices are excluded according to the implemented rules.

The score is not assessed if no eligible feature remains. Counts of every category and exclusions, with examples, remain visible or downloadable. This annotation denominator differs from all deposited feature positions used by cell-based QC. Sample eligibility or class edits do not rename features or change the annotation calculation.

### 11.2 Named features mapped to RefMet

Score = `100 × named matrix features with a valid RefMet match / named matrix features`. Feature names are matched to metabolite records from the **same analysis**, after trimming, lowercasing, collapsing spaces, comma-to-underscore and apostrophe-to-caret normalisation.

Blank and invalid RefMet names are discarded first. Exactly one distinct valid RefMet name among matching records means mapped; several different valid names mean conflicting/unmapped. No named matrix feature means not assessed. MERIT-ML does not add a new chemical identification. This feature-based score and FAIR's record-based mapping can differ without being contradictory.

Details show mapped/unmapped/conflicting counts, reasons and examples, distinct mapped RefMet names, shared names and feature-level lists. Several ions, retention-time variants or features can map to one RefMet term; the distinct-name count need not equal the mapped-feature count.

### 11.3 Metabolite classes and repeated feature names

The metabolite-class block summarises RefMet superclasses; main/subclass information and per-feature assignments are in downloads. Classification coverage is limited by the available mapping/reference data, and a superclass is not a confirmed pathway result.

Repeated feature labels are grouped using normalised label keys; supported duplicate suffixes such as `.1` or `(2)` are ignored for named features. Otherwise an RT, ion or isotope suffix can distinguish labels. The block also identifies different labels sharing one RefMet name. Neither a repeated label nor a shared RefMet term proves an accidental duplicate analyte. Examples, group sizes and full JSON lists are provided. These counts do not enter either annotation score.

## 12. Label Structure and Class Support metrics

### 12.1 Class support

Let m be the smallest selected class, M the largest, and k the selected number of CV folds. Set `p = m / (m + M)` and `H(p) = −p log2(p) − (1 − p) log2(1 − p)`. The score is:

`Class support = 100 × H(p) × min(1, m / k)`.

The entropy term describes balance of the **smallest against largest** class, rather than entropy across all classes. The second term describes how far the smallest class reaches the fold count. At default k = 5, 10 versus 10 gives 100, 3 versus 3 gives 60, and 100 versus 200 gives about 91.8. One class gives zero. No labelled eligible samples or a selected class missing from this matrix makes it unavailable.

Higher means stronger support under this explicit heuristic; it is not a validated prediction of accuracy, power or estimate stability. It does not enter the analytical alerts. Class details show the balance and fold terms separately, samples left out and the factor/joint-class tables.

### 12.2 Cross-validation sample-count support

The tab can label this binary scored item **Cross-validation feasible**. Its precise meaning is the **count requirement**: 100/met when there are at least two classes and every class contains at least k distinct labelled eligible sample IDs; otherwise 0/not met. k defaults to **5**, adjustable **2–10** in steps of 1. Exactly k meets the condition. If the selected classes cannot be assessed, it is unavailable instead of 0.

This checks whether each class can appear in every stratified test fold. It does not execute cross-validation, train a model, certify independent subjects, assess statistical power or guarantee stable estimates. Five samples per class can meet the minimum yet be insufficient for a useful scientific model. Grouped subject-aware validation can have different requirements.

Changing k affects the count condition, the fold term of Class support, the profile and dependent guidance. It does **not** change the separability-neighbour setting, raw analytical scores, R1 reference or annotation/FAIR values.

## 13. ML Task Readiness metrics

These results use the selected classes. Separability is the section's one scored item, displayed with its chance level. Neither separability nor feature associations determine analytical alerts or replace validation of a model.

### 13.1 Class separability

Features are internally converted to average ranks across measured positions, with missing entries assigned the feature's mean rank, then centred and scaled per feature. Constant/no-variation features contribute no rank separation. Repeated positions of a sample ID are averaged into one profile. Distances are Euclidean on these profiles. This internal representation supports unit-agnostic comparisons; it is not an imputation/normalisation recommendation or a transformation of the exported deposited matrix.

Let K be the **number of nearest neighbours** (default 7) and θ the overlap threshold (default 3). For each class c, R(c) is the share of its samples with **more than θ** of the K neighbours belonging to other classes. Score = `100 × mean over classes of (1 − R(c))`. Every class gets equal weight. Higher means stronger local separation under the current labels; it is not cross-validated model accuracy.

K can be **3–15** in steps of 1. θ is a whole number, at least zero and at most **floor(K/2)**. The displayed input range extends to 7; its effective upper bound follows K and larger entries are capped. Distance ties at the K-th neighbour are handled by an exact tied-neighbour calculation; a relative tolerance of `1e−5` accounts for stored precision.

The score is not assessed with fewer than two classes, an absent selected class, no varying usable feature, at most K samples in total or a class with fewer than **K − θ + 1** samples. The default K=7, θ=3 therefore needs at least five samples in each class, but this is a separability requirement distinct from the CV-fold control. For matrices above 4,000 sample IDs the retained neighbourhood has 200 neighbours per sample; selecting a narrow task can leave too few retained neighbours for an exact result, which is reported.

The rank representation is constructed for the matrix; class selection and eligibility changes select profiles rather than rebuilding a new rank transformation for each task. Consequently this is a diagnostic on the assessed matrix, not a leakage-safe feature-transform pipeline for estimating predictive performance.

### 13.2 Chance level and overlap details

Chance is the expected score if class labels were randomly assigned while preserving class sizes. For a class with n_c of N samples, the other-class-neighbour count follows a hypergeometric distribution with population N−1, other-class population N−n_c and K draws. Chance = `100 × mean over classes P(other-class neighbours ≤ θ)`.

Always read separability beside chance. Strong imbalance and different class counts can change the chance value. The report can show class-specific overlap and chance, the most overlapping class pair, samples surrounded by other-class neighbours and tie evidence. These are exploratory label/data diagnostics, not evidence that samples are mislabeled or that a causal biological difference exists.

### 13.3 Features associated with selected classes

This informational block tests whether measured feature values differ between the selected classes. It reports the number/share tested and significant, test name, adjusted and raw p-values, direction where meaningful, class medians/counts, examples and reasons features/tests are unavailable. It is not automatic feature selection, model feature importance, a readiness score or proof of a disease biomarker.

Without confirmed subject information, the default is **Kruskal–Wallis**, using one value/profile per sample ID and testing a feature only when every selected class has at least **five measured values**. Missing values are excluded for that feature. These statistical tests use the rank-based representation described above; class medians/directions are presented from deposited values for the observations included by that feature's test.

With real subject IDs confirmed:

- When no subject spans classes, a supported independent comparison uses one value per subject; same-subject repeats do not count as new independent subjects.
- For two classes with shared subjects, the supported paired comparison is the **exact Wilcoxon signed-rank test** on complete subject pairs. Zero differences are omitted. At default q=0.05, at least **six non-zero pairs** are needed; the minimum follows `ceil(1 + log2(1/q))`.
- For more than two repeated classes, the supported **Friedman** test requires at least **11 complete subjects and seven classes**. Unsupported or insufficient designs are not assessed with subject adjustment.

For each feature, displayed medians and counts use exactly the subjects/observations retained by that feature's test, not every subject in the original table. Partial subject coverage, no complete pairs/blocks, too few non-zero differences, constant data or unsupported repeated designs have explicit reasons. Confirmation can reduce the assessable set rather than improve a score.

P-values for the tested features are adjusted by **Benjamini–Hochberg**. q defaults to **0.05**, adjustable **0.01–0.20** in steps of 0.01. Changing q changes the significance criterion and can change paired-test minimum support; it does not change class separability or the analytical alerts. Interpret two-class direction from the displayed class names/medians; a multiclass association need not have one meaningful up/down direction. Examples are limited; the strongest stored/available results and downloadable evidence should not be mistaken for every possible biomarker.

### 13.4 Features per sample and subject information

The task p/n uses the matrix's feature count and distinct eligible sample IDs in the selected classes; a zero selected-sample denominator is unavailable, not infinity used as advice. p/n elsewhere can use a whole-matrix or different sample denominator. High p/n raises overfitting and feature-selection considerations, not an automatic failure threshold.

Subject information reports coverage, distinct subjects, subjects with several samples and subjects spanning classes. Deposited unconfirmed IDs trigger verification advice; confirmed sharing calls for subject-disjoint validation. A single confirmed subject cannot support testing generalisation to unseen subjects. No subject information is not proof of independence. These warnings are separate from the numerical sample-count condition.

## 14. All numerical controls

Open **Show tools** to see the current settings and Help. Apply a change to recompute the relevant outputs. The CV-fold control is prominent because it directly changes count support; it is a different k from separability neighbours. Settings shared between report sections are one active choice for the study.

The table lists every numerical setting under **Show tools**. **Default**, **minimum**, **maximum** and **step** describe the values you can enter. The assessment download records the settings applied to your results.

| Setting | Default | Minimum–maximum | Step | Effect / constraint |
|---|---:|---|---:|---|
| Cross-validation folds k | 5 | 2–10 | 1 | Class-count requirement and Class support fold term. |
| Structural: sample-support target N | 20 | 5–100 | 1 | Structural item and mean; not CV count support. |
| Low-information: frequency ratio R | 19 | 5–99 | 1 | Flag when ratio > R and distinct percentage ≤ U; also changes redundancy eligibility. |
| Low-information: distinct-value percentage U | 10 | 1–25 | 1 | Flag when percentage ≤ U and ratio > R; also changes redundancy eligibility. |
| Low-variation caution: CV (%) | 1 | 0.1–5 | 0.1 | Informational only; strictly below the threshold. |
| Redundancy: absolute correlation c | 0.9 | 0.8–0.99 | 0.01 | Evaluated pairs above c, with 1e−5 equality tolerance. |
| QC reproducibility: RSD (%) | 20 | 10–40 | 1 | Informational pass counts; detection must still be >70%. |
| Outliers: fence multiplier a | 1.5 | 1–3 | 0.1 | Sample medians outside Q1−a·IQR / Q3+a·IQR. |
| Extreme-alert percentile | 2.5 | 2.5–10 | 2.5 | Choose 2.5, 5, 7.5 or 10 from R1. |
| Low-alert percentile | 10 | 2.5–10 | 2.5 | Choose 2.5, 5, 7.5 or 10; effective value never below extreme. |
| Separability: neighbours K | 7 | 3–15 | 1 | Neighbour count, not CV folds. |
| Separability: overlap threshold θ | 3 | 0–7 | 1 | Integer; effective maximum floor(K/2), although input maximum is 7. |
| Feature associations: FDR q | 0.05 | 0.01–0.2 | 0.01 | Significance and paired-test minimum support. |

The θ cap and permitted alert percentiles in the notes are additional constraints. There are **13 numerical settings plus eight optional custom cut-off fields**. These are controls, not additional scored metrics. All other fixed requirements, such as detection >70%, at least three values, minimum shared-pair samples and MetaBatch-style coverage, are described with their metrics rather than exposed as sliders.

### 14.1 Custom alert score cut-offs

For **each of four checks**, enter an optional **extreme** and/or **low** score cut-off: eight fields. Allowed numbers are **0–100**, including decimals. Empty means use the selected R1 percentile. Non-numeric, non-finite or out-of-range entries are ignored in favour of the reference and their ignored status is reported. An alert requires the score to be **strictly below** its cut-off.

If the low cut-off is below the extreme cut-off, the effective low cut-off is raised to the extreme one; low cannot be narrower than extreme. A custom cut-off is identified as custom where relevant and is retained in the assessment settings. An extreme cut-off of zero cannot flag a non-negative score. A cut-off of 100 can flag every assessable score below 100, not scores tied at 100.

Changing these values changes alert assignments and guidance, not raw metric scores. It does not create a new reference cohort or rewrite R1. Restoring the defaults clears the custom fields. The two shared editors use the applied values in the report and exports.

### 14.2 What each change can affect

| Change | Outputs that depend on it | Outputs not recalibrated by that change |
|---|---|---|
| CV folds | Count support; Class support fold term; profile; related guidance | Analytical scores, R1, separability neighbours, annotation/FAIR |
| Selected factors/classes | Class counts/support; separability/chance; feature tests; selected-task p/n; other-factor comparison; task narrative | Label-independent analytical checks and annotation/FAIR |
| Sample eligibility/exclusion | Applicable Structural/analytical metrics; class metrics; selected-task outputs and guidance | Deposited source values and reference R1 |
| Factor-label edits | Assignment and factor selection; related control/eligibility evidence where the rules use it; class-dependent outputs | A pure label change is not permission to filter analytical QC to selected classes |
| Subject edits/confirmation | Subject evidence and supported subject-aware feature tests | Count support, separability and analytical scores |
| Batch-like edit | Sample-annotation fields and associated informational warnings | Analytical scores, class labels and FAIR index |
| Low-information R/U | Flagged features and score; evaluated redundancy features; alerts and details | Reference cut-offs themselves |
| CV caution C | Informational low-variation count/list | Low-information score and alerts |
| Redundancy c | Exceeding pairs, selected features, redundancy score and related alerts | Other analytical scores |
| Outlier multiplier | Fences, flagged sample medians, outlier score and related alerts | Other analytical scores |
| QC RSD cut-off | Per-feature QC pass/fail counts | Headline median RSD and analytical alerts |
| Separability K/θ | Separability, chance and overlap details | CV fold count and feature-test q |
| Feature-test q | Adjusted-significance summaries and q-dependent paired support | Separability and analytical alerts |
| Alert percentiles/custom cut-offs | Alert tiers, counts, summary and corresponding narrative | Metric values, classes, R1 source distributions |
| Theme/tour/Show numbers/details | Presentation | All scientific inputs and results |

An unavailable/resource-limited recomputation remains unavailable with a reason. It must not be inferred to have succeeded because a control accepted a value. Save the assessment record to document the effective settings and omissions.

## 15. R1 and alert interpretation

The analytical checks compare your matrix with a **reference collection of scores**, labelled **R1** in the interface. This reference contains scores from 11,749 matrices. It gives a consistent comparison for any public study ID you assess; your study does not need to belong to that collection. It is not an overall readiness score or a threshold trained to predict model accuracy. The reference counts matrices, so a study with several analyses or data sources can contribute several observations.

At defaults, an assessable score strictly below its **2.5th-percentile** cut-off is extreme. A score below its **10th-percentile** cut-off but not extreme is low. Exact equality is not alerted at that boundary. Missing/unassessed scores do not enter the percentile calculation or become passes. Ties mean the share flagged need not equal the nominal percentile.

| Percentile | Missing entries and zeros | Low-information features | Redundant features | Outlier samples |
|---|---:|---:|---:|---:|
| 2.5 (default extreme) | 30.30 | 68.91 | 21.38 | 80.00 |
| 5 | 42.22 | 87.50 | 28.57 | 83.33 |
| 7.5 | 50.00 | 93.60 | 34.19 | 86.67 |
| 10 (default low) | 57.01 | 96.97 | 40.00 | 88.24 |

Reference populations with an assessable value: Missing entries and zeros: **11,717**; Low-information features: **11,518**; Redundant features: **10,367**; Outlier samples: **11,591**.

The table shows rounded cut-offs; full precision is used in calculation. Each percentile uses only matrices where that particular metric could be assessed. The chosen default low and extreme percentiles can each be **2.5, 5, 7.5 or 10**, with low never below extreme. Selecting another percentile changes which reference cut-off is used; it does not recalculate the reference from the current study.

The low boundary controls the total set reviewed; moving only the extreme boundary redistributes flagged matrices between extreme and low tiers. Several checks can flag one matrix, so their alert counts cannot be added to obtain unique matrices. The summary uses the strongest tier and shows how many checks were assessed.

Assessing a new study does not change the default comparison. You can choose another reference percentile or enter your own score cut-offs. These choices change which results raise alerts, without changing the comparison collection itself. The assessment download records the reference and any custom settings used.

The reference describes all assessed Workbench matrices, not experimentally matched peers. Endpoint, platform, sample size, preprocessing and feature composition can affect these distributions. An extreme alert is a prompt for specific review, not a declaration that the matrix is unusable. No alert means no assessed value crossed these relative boundaries; it is not certification.

## 16. Downloads and provenance

### 16.1 Assessment JSON

Use **Download MERIT-ML Assessment JSON** at the report top. It records current per-analysis/endpoint results, supporting counts, applied parameters, class/sample edits, subject confirmation and reference/custom cut-off information. The profile does not assign one universal readiness score. Save records when comparing tasks or settings; the on-screen page is not a publication archive.

### 16.2 Matrix with classes in force

Data at a glance offers an endpoint-named **matrix CSV**. It has one row per ML-eligible **position**, with sample ID, the original position index (starting at 1 among matrix sample positions), subject ID, class in force, deposited factor string and one column per feature. Repeated sample IDs can therefore produce several rows. The exported subject reflects the supported effective subject handling; retain the accompanying assessment record when documenting edits.

All eligible rows are retained, including rows **outside the selected classes**, whose class field is empty. Selecting two classes does not automatically drop every other eligible row from this export. The caption explains this; filter non-selected rows explicitly for a selected-task model. QC/blank/standard-like positions, alignment positions and explicitly excluded samples are not eligible matrix rows.

Numeric value cells remain as deposited; this is not imputation, scaling, normalisation, feature removal or subject aggregation. Do not feed duplicate positions or unassigned rows blindly into a model. The download name describes its matrix/classes rather than certifying it as model-ready.

### 16.3 Deposited-position matrix ZIP and source inputs

The per-matrix input download can preserve every deposited position with status/eligibility and feature cells, together with a manifest. This is distinct from the eligible-row CSV. Use the export's column definitions and manifest to distinguish full-position data from modelling-subset data. Underlying source downloads preserve endpoint identities; datatable, mwTab and untarg_data are not silently merged.

The browser's input/run-record downloads include the source bodies actually retained, checksums, retrieval information and omissions. A refused or interrupted body does not receive a fabricated full-file hash. A bounded metadata-only view is identified as derived, not presented as the complete raw response. A run record is not the same as the smaller assessment JSON.

### 16.4 Feature lists and other detailed downloads

The small **download the full list (JSON)** controls generate analysis × endpoint lists for the active inputs. Available kinds include controls, low-information/CV cautions, redundant features/pairs, outlier samples, QC reproducibility, annotation coverage/mapping, metabolite classes and repeated-name groups. Acquisition/context evidence has its own structured download. Lists show supporting reasons or categories rather than another overall score.

Lists are built when requested, avoiding the need to embed every large list in the page. Building a download still needs computation and memory; it does not remove the storage needed for the current study's underlying inputs and calculations. Large exports can fail their resource check while the assessment remains valid and visible.

### 16.5 Reproducibility and data handling

Record the study/analysis/endpoint, retrieval date and source hashes, software and reference details, applied settings, class choice, edits, omitted checks and exported data's scope. Workbench can change deposited records; a later fresh fetch can yield a different matrix. Report whether compared results came from the same fetched inputs. Downloaded source data remain attributable to the repository and study authors; a local export does not grant permission to redistribute those inputs.

## 17. Bulk assessment and local Docker use

### 17.1 Assess several studies locally

The landing-page bulk field accepts typed/pasted study IDs, normalises them and removes duplicates, with a maximum of **500 IDs** per submitted set. Review the validation results before running. Available IDs are green, invalid/unavailable IDs red, explicitly confirmed embargoes amber and unverified entries grey. A failed public-availability request is not proof of an embargo. Only available accepted IDs are submitted.

The completed bulk view gives summary tables/downloads and underlined **individual study-report links**. Open each in a new tab to inspect and interact with the ordinary report. The link retains the bulk job's settings/edits where applicable; later interaction in a study report does not silently rewrite the already completed bulk summary. Reports require retained job files; expired/deleted jobs have an explicit message.

Bulk assessment is disabled in the browser application, with a link to the local Docker application. Use the browser to assess one study at a time, or Docker to assess several studies in one run.

### 17.2 Use the local application

You can use MERIT-ML in your browser without installing anything. The local Docker application is an alternative for bulk work or matrices that do not fit in the browser.

1. Install Docker for your operating system and make sure it is running.
2. Open the [MERIT-ML installation instructions](https://github.com/BiosystemEngineeringLab-IITB/merit-ml) and follow the launch command for your system. Python and its scientific dependencies are included in the application.
3. Open the local web address printed by the launcher. It selects an available port; you do not need to choose one in advance.
4. Enter a public study ID and assess it in the same way as on the website.

On Linux, use `sudo` before a Docker command if your account requires it. On Windows or macOS, use the appropriate Docker terminal and omit `sudo` where it is unavailable. The installation page also explains how to stop, restart or update the application.

Only studies you request are downloaded. Docker keeps their inputs and results in a storage volume on your computer, which can grow as you assess more studies. Export results you want to keep. Stopping the application does not remove that volume; deleting the volume removes the data in it. Completed reports normally expire after 24 hours unless you configure the application to retain them. Internet access is needed to retrieve study inputs and external evidence.

### 17.3 Advanced settings for the local application

| Environment setting | Default | Meaning |
|---|---|---|
| `MERIT_WORKER_MEMORY_MB` | Automatic, 65% of available computer/container memory | Per-worker MiB budget; 0 chooses automatic. Leave memory for the rest of the application and other processes. |
| `MERIT_CHECK_TIMEOUT_SECONDS` | 1800 seconds | Time limit for an individual calculation/check; 0 disables this timeout. |
| `MERIT_RENDER_TIMEOUT_SECONDS` | 3600 seconds | Maximum wait for report/export rendering. |
| Docker memory allocation | Your container/host setting | Overall memory still bounds the application; increasing a per-worker setting does not create RAM. |

These advanced settings control computing resources, not the scientific thresholds under Show tools. Execution records report the memory budget, elapsed time, peak memory use and reasons for failures or timeouts. The local application can process matrices beyond the browser limits when your computer has enough resources. It still cannot guarantee completion of every matrix on every computer. The standard installation is intended for use on your own computer.

## 18. Browser limits and partial results

The browser uses **Pyodide/WebAssembly** and JavaScript workers to run the assessment with your compute. The application and reference files needed for assessment are downloaded as needed. A study is not refused merely because one matrix is large: the guard considers each analysis × endpoint and, where applicable, each check. Successful results are retained and independent work continues.

The browser estimates the memory needed for each matrix. The estimate starts with **330 MiB** for the application and adds the expected costs of cells, input files, feature pairs and sample comparisons. A small feature-pair check helps estimate the cost when many features are correlated. The memory budget is **1 GiB**, or **0.5 GiB** when a device reports less than 4 GB memory. These are estimates, not measured universal peak-memory guarantees. Device memory can be unavailable or approximate.

Input responses are streamed with a **50 MB** limit; a declared oversize response can be refused before collection, and collection stops if the streamed body crosses the limit. ZIP, CSV/text and JSON output construction also has a **50 MB** browser limit, with additional pair-list preflight checks. A small report can therefore coexist with an unavailable large download.

When a memory/file restriction applies, a prominent notice names the affected endpoint/analysis and links to the full application. **Blank does not mean absent.** The report distinguishes absent data, failed transfers, refused matrices, missing shared metadata and checks that could not be assessed. Datatable zero provenance is not guessed when the necessary mwTab evidence was unavailable.

Saved checkpoints on the user's device retain completed tasks and omission/fetch provenance during a run. Worker replacement can recover from a failed calculation and continue remaining work. This does not guarantee recovery after the operating system kills the entire tab, browser storage is unavailable or disk quota is exhausted. Keep the tab open and export important completed results. Use Docker on a sufficiently resourced computer for a browser-refused matrix.

Eligibility/low-information changes can require expensive pair recomputation; a resource-limited interactive check is reported as unavailable. A pure class or subject edit does not by itself justify discarding a label-independent matrix. The main report and Data at a glance should identify incomplete work rather than reporting fabricated zero/pass values.

## 19. Troubleshooting and practical interpretation

| What you see | What to do |
|---|---|
| Study rejected/unavailable | Check ST accession and public release; distinguish explicit embargo from an availability-request failure. Retry a network failure. |
| Metadata-only report | Inspect which endpoints lacked a readable quantitative block. Raw files/attachments are not automatically processed into a matrix. |
| No ML-eligible samples | Review controls, alignment and edits; no class/cell interpretation should be inferred from an empty eligible subset. |
| Cannot assess class counts | Check labelled eligible IDs and whether every selected class occurs in this particular matrix. Study-wide selector counts are not its matrix counts. |
| Count requirement not met | Review class sizes, scientific task and folds. A different grouping asks a different question; relabelling or synthetic oversampling does not supply new independent test subjects. |
| No alerts among fewer than four assessed checks | Read missing-check names/reasons; this is incomplete coverage, not all checks passing. |
| Extreme analytical alert | Read the named metric's absolute burden, denominator, settings and evidence. Handle missing/zero representation or appropriate feature/sample review before modelling. |
| Strong separability | Compare chance; review labels, subject dependence, other factors, batch and external validation. This is not measured test-set performance. |
| Shared/unverified subjects | Verify the identifier; if real, keep subjects disjoint in downstream validation. Do not assume “Human” or a group name identifies a person. |
| A chosen class has no samples after edits | Change the task or restore relevant eligibility only if justified; do not silently ignore the absent class. |
| Browser refusal or stopped worker | Keep completed results, read the named matrix/check and reason, and use the local application for omitted work. |
| Export fails but report remains | Check output/pair-list resource limits; calculation and export availability are separate. |
| Slow assessment | Allow for repository/network delays and feature-pair calculations; read current step and Generating report. Do not assume a quiet interval proves completion. |

A practical review order is: verify source/matrix identity and Structural evidence; choose and justify classes; inspect eligible samples/subjects and count support; review analytical burdens and scale; then assess annotation/FAIR evidence for the interpretation or cross-study reuse you intend. Missing-value handling and feature selection for performance estimation belong **inside training folds**. Do not use MERIT's diagnostic feature associations as feature selection on the whole dataset before cross-validation.

MERIT-ML does not verify the complete acquisition/preprocessing pipeline, fit a predictive model, measure learning-curve stability, certify power, detect every mislabeled sample, rule out all confounding, confirm metabolite identities, guarantee privacy/fairness or validate independent-cohort performance. The profile is evidence for review under stated inputs and rules.

## 20. Complete metric and evidence checklist

Use this checklist to find each metric and its supporting information. Some rows describe summaries, controls or informational results rather than additional scored metrics.

| Item | Role | Guide section |
|---|---|---|
| 1. Sample support | Scored | 6 |
| 2. Samples with a factor label | Scored | 6 |
| 3. Numeric value cells | Scored | 6 |
| 4. Distinct sample IDs per position (tab: Duplicate sample IDs) | Scored | 6 |
| 5. Structural mean | Separate summary | 6 |
| 6. Linkage, completeness and agreement | Informational | 6 |
| 7. Study metadata | Scored | 7 |
| 8. RefMet mapping of deposited metabolite records | Scored | 7 |
| 9. External IDs via RefMet | Scored | 7 |
| 10. Mass/RT metadata | Scored (0/100) | 7 |
| 11. LC-method context | Scored | 7 |
| 12. FAIR index | Separate summary | 7 |
| 13. Deposited evidence | Informational | 7 |
| 14. Missing entries and zeros † | Scored; alert | 8–10 |
| 15. Low-information features † | Scored; alert | 8–10 |
| 16. Redundant features † | Scored; alert | 8–10 |
| 17. Outlier samples † | Scored; alert | 8–10 |
| 18. Units declared | Scored (0/100) | 8–10 |
| 19. Analytical review alerts | Readiness profile | 8–10 |
| 20. Sample eligibility and controls | Informational | 8–10 |
| 21. QC reproducibility | Informational | 8.2 |
| 22. Low-variation caution | Informational | 8–10 |
| 23. Measurement scale | Informational | 8–10 |
| 24. Acquisition, data summary and processing | Informational | 8–10 |
| 25. Sample annotations (MetaBatch-style) | Informational | 8–10 |
| 26. Additional sample data | Informational | 8–10 |
| 27. Evidence behind the scored items | Informational | 8–10 |
| 28. Named-feature coverage | Scored | 11 |
| 29. RefMet mapping of named features | Scored | 11 |
| 30. Labels, mapping, classes and repeats | Informational | 11 |
| 31. Class support | Scored | 4, 12 |
| 32. Cross-validation sample-count support (tab: Cross-validation feasible) | Scored (0/100); readiness profile | 4, 12 |
| 33. Classes, factors and exclusions | Informational | 4, 12 |
| 34. Class separability | Scored | 13 |
| 35. Chance level | Shown with the score | 13 |
| 36. Features associated with the selected classes | Informational | 13 |
| 37. Overlap details, p/n and subjects | Informational | 13 |
| Search, availability, progress and Generating report | Interface / informational | 1, 3 |
| Light/dark mode, About, publication, GitHub and manual | Interface / informational | 1 |
| Search/report tours, replay and opt-out/storage limits | Interface / informational | 1.2 |
| Per-matrix profile, coverage, tiles, colour/help and grouped endpoints | Interface / informational | 3 |
| Data at a glance and applied-input recommendations | Interface / informational | 3.2, 19 |
| Derived design/analyzer/acquisition context and provenance | Interface / informational | 3.3, 10.3 |
| Whole-string / selected-factor classes and observed combinations | Interface / informational | 4 |
| Other-factor states, proportions and joint distributions | Interface / informational | 4.3 |
| Use as, labels, batch-like edits, subject shortcuts and confirmation | Interface / informational | 5 |
| Thirteen numerical settings, bounds, steps and dependencies | Interface / informational | 14 |
| Eight custom score cut-offs, fallbacks and precedence | Interface / informational | 14.1 |
| Reference-based alerts, strict comparisons, ties and custom cut-offs | Interface / informational | 15 |
| Assessment JSON, matrices, full-position exports and feature/evidence lists | Interface / informational | 16 |
| Bulk validation/statuses, individual interactive reports and expiry | Interface / informational | 17 |
| Local memory/time settings and automatic choice of address | Interface / informational | 17.2–17.3 |
| Browser per-matrix guards, downloads, checkpoints and omissions | Interface / informational | 18 |
| Unavailable / partial / metadata-only states and troubleshooting | Interface / informational | 2, 3, 18–19 |
| Update thresholds: optional local reference calculation, approval and restoration | Interface / optional local operation | 23 |

## 21. Getting help and keeping your results

Save the assessment JSON and, where available, the run record when you want to compare results or ask for help. Include the study ID, analysis and data source, applied settings, chosen classes, sample edits and the exact error message. State whether you used the browser or Docker and which checks completed.

Workbench records can change. Keep the retrieval date and the input details in your download when you need to reproduce an assessment. The record also identifies the alert reference and the settings used, so another user can understand how you obtained the result.

Never send passwords, tokens, private keys or unrelated private sample information when requesting support.

Open [MERIT-ML](https://merit-ml.in/) to assess a study, [read the publication](https://doi.org/10.26434/chemrxiv.15004429/v2) for the scientific methods, or use the [installation instructions and issue tracker](https://github.com/BiosystemEngineeringLab-IITB/merit-ml) for help with the local application. This guide is available as searchable HTML, downloadable PDF and editable Markdown.

## 22. How the web tool works

### 22.1 Website delivery and browser execution

MERIT-ML is available at [merit-ml.in](https://merit-ml.in/). The hosted assessment interface is a static HTML/JavaScript application delivered through Vercel. Assessment takes place on the user's device: a dedicated Web Worker loads CPython compiled to WebAssembly through Pyodide, together with NumPy, SciPy, pandas, scikit-learn, lxml and the additional Python packages required by MERIT-ML. The worker runs the Python assessment and report-generation code, while the main browser thread handles navigation, progress messages, controls and report display. The hosted assessment therefore does not submit a study to a remote Python calculation service. The browser and Docker distributions share the scientific calculation and report code; browser-specific adapters provide networking, filesystem access, execution scheduling, recovery and resource limits. The [getting-started instructions](https://merit-ml.in/live/manual/#1-start-here) describe the user workflow.

### 22.2 Public-study lookup and live input retrieval

The user enters a Metabolomics Workbench study accession. MERIT-ML checks its format and public availability using the repository's current study registry; an accession absent from that list can be checked against its individual analysis registry. Invalid identifiers, unavailable studies and failed requests are reported separately where the available evidence permits. MERIT-ML does not bypass repository access restrictions.

Once the study is accepted, the analysis registry supplies its analysis identifiers. Browser requests retrieve study summary, factors, metabolite records and disease metadata, together with the repository study page used for contextual evidence. For each analysis, the application requests mwTab in text and JSON formats, the quantitative `/datatable/` response and `untarg_data`. It does not substitute `/datatable/file/` for `/datatable/`, and it does not convert raw spectra or arbitrary Excel attachments into feature matrices. The registry's study identifiers and analysis-identifier formats are checked before assessment. A missing endpoint, a response without a valid quantitative matrix and a refused or failed transfer remain distinct outcomes. [Data sources and denominators](https://merit-ml.in/live/manual/#2-studies-analyses-data-files-and-denominators) explain how each result is interpreted.

Study responses are fetched directly from the repository using browser networking. Quantitative input transfers use the JavaScript Fetch API and stream the response body; Python-side requests needed for discovery or supplementary evidence use a browser XMLHttpRequest adapter inside the worker. These requests are subject to the repository's availability and browser cross-origin rules. Streamed transfers have a 180-second inactivity limit and a ten-minute overall transfer limit. Fetch records retain the requested and final URLs, timing, exposed response headers, HTTP status, received byte counts and errors. Complete response bodies receive a SHA-256 digest; interrupted responses retain a received-byte digest explicitly identified as partial.

### 22.3 Software and reference inputs

The application downloads its Python code and required packages when the browser engine starts. Static chemical-reference tables and the fixed R1 analytical-alert reference are supplied separately from study responses. Declared contextual reference material is also loaded where applicable, including reviewed study-design or acquisition evidence; applicable study-specific reference files are checked against a SHA-256 and byte-size index. Context derived from such reference material is distinguished from context calculated from newly retrieved inputs. These reference assets do not constitute a precomputed readiness assessment for the requested study. Study matrices are retrieved and the scientific metrics are calculated for the current run.

The browser's CacheStorage API retains selected software and reference downloads under application-specific names, reducing repeat transfers. This reusable asset storage is separate from the study-input and result checkpoints described below. Changing the user's class definition or analytical settings does not modify the distributed reference tables. [R1 and alert interpretation](https://merit-ml.in/live/manual/#15-r1-and-alert-interpretation) explain the distinction between the comparison reference and user-adjustable alert boundaries.

### 22.4 Local storage and checkpointing

Downloaded study responses are written as Blob objects to IndexedDB on the user's device. Python reads the inputs through its browser-based virtual filesystem. New computational outputs use the memory-backed filesystem, while restored larger files are mounted through Emscripten WORKERFS as read-only Blob-backed files. A bounded amount of smaller data is preloaded; files that require modification are copied into writable storage. This arrangement reduces unnecessary copies into the WebAssembly heap.

After each initial assessment task, completed outputs and execution records are saved to IndexedDB. Study-specific quantitative inputs and calculated outputs are not uploaded to the hosted MERIT-ML application or its reference-object storage by this assessment workflow. Metabolomics Workbench receives the requests for its data, and the infrastructure delivering the software and reference assets receives the corresponding asset requests. Local storage remains subject to the browser's quota, eviction and private-browsing policies; it is not a permanent research archive. [Downloads and provenance](https://merit-ml.in/live/manual/#16-downloads-and-provenance) describe the records users can save explicitly.

### 22.5 Per-matrix assessment and failure isolation

Each analysis × endpoint is treated as a separate matrix. Before full calculation, MERIT-ML estimates computational demand from matrix dimensions and input size, including feature-pair and sample-pair storage. A deterministic probe of up to 1,000 features can refine the feature-pair estimate. The admission budget is 1 GiB, reduced to 0.5 GiB when the browser reports less than 4 GB of device memory; these are application limits and estimates, not guarantees about available physical memory. Quantitative responses and downloadable outputs have a 50 MB safety limit. An oversized mwTab response can still be streamed to retain a bounded, explicitly identified metadata-only view.

Admitted matrices pass through input parsing and structural checks, sample/control identification, analytical QC, annotation, label structure, class separability and the evidence needed to construct the readiness profile. The scientific stage functions are shared with the local application. Browser execution schedules these tasks sequentially rather than using Python thread-pool concurrency. Each completed task is merged into the assessment record before the next task proceeds. The main page receives progress messages from the worker and displays a separate **Generating report** step after calculation.

A size refusal or computational failure is recorded for the affected matrix or check, retaining successful work elsewhere in the study. Checks that depend on an unavailable input are also identified: for example, datatable zero provenance is not inferred when the paired mwTab input cannot be assessed. Shared metadata failures are marked as shared. If a worker stops during a task, the page can replace it, restore completed checkpoints, mark the interrupted task as not assessed and continue. The worker can also be recycled between matrices when its allocated heap exceeds 80% of the configured budget. Browser-wide failure or exhausted storage can still prevent recovery. [Browser limits and partial results](https://merit-ml.in/live/manual/#18-browser-limits-and-partial-results) describe these outcomes and the local alternative.

### 22.6 Interactive recalculation and report generation

The worker generates the report using MERIT-ML's Python HTML-rendering functions. The main page places that HTML in a report iframe and waits for its document-ready message before declaring the report ready. Forms and download actions in the report are intercepted and sent to the worker as structured messages containing the active run, study accession, settings and sample/class edits. The corresponding Python functions recalculate the affected results and return updated HTML or a downloadable output. This is local computation in the browser, rather than a form submission to a remote calculation backend.

Dependencies follow the metric definitions. The cross-validation fold count changes sample-count support and the fold-dependent class-support term. Class definitions affect class-based results without restricting the label-independent analytical checks to the chosen classes. Sample-eligibility changes affect diagnostics evaluated on eligible positions. Low-information and correlation settings can alter redundancy; alert-boundary changes alter the alert interpretation. Data at a glance and the readiness profile are regenerated from the applied inputs. See [choosing classes](https://merit-ml.in/live/manual/#4-choose-the-classification-task), [sample and subject edits](https://merit-ml.in/live/manual/#5-adjust-samples-labels-batches-and-subjects), [all numerical controls](https://merit-ml.in/live/manual/#14-all-numerical-controls) and [Data at a glance](https://merit-ml.in/live/manual/#3-2-data-at-a-glance).

### 22.7 Closing, cancelling or starting another study

Users should keep the tab open until the report is ready. Closing or reloading it stops the current worker and discards the active page and in-memory Python state; computation does not continue in the background after the page closes. IndexedDB checkpoints and reusable software/reference assets can remain on the device. Their presence does not provide an automatic resume interface after reopening the site, and it does not guarantee that the current report's edits have been preserved. Recovery from a replaced worker while the page remains open is distinct from resuming a closed browser session.

Starting another accepted study creates a fresh calculation worker and clears the previous study's IndexedDB file store when the new input-retrieval run begins. Cancelling terminates the current worker but does not immediately erase its saved checkpoints. To remove retained browser data explicitly, users can clear this site's data in their browser settings; this also removes saved interface preferences. Assessment downloads remain in the user's chosen download location until the user removes them. Save the assessment JSON and any required run record before leaving the page. [Keeping results and requesting help](https://merit-ml.in/live/manual/#21-getting-help-and-keeping-your-results) lists the information needed to reproduce or report a problem.

### 22.8 Latency, local execution and reproducibility

The first visit includes loading the Python/WebAssembly runtime, packages, code and reference assets. Each study then requires multiple repository requests, whose number grows with the number of analyses. Quantitative transfers currently run sequentially, so repository response times accumulate. Parsing, ranking, feature correlations, sample distances, statistical tests, report generation and interactive recalculation use the user's processor and memory. Assessment time therefore depends on repository response time, network speed, the number and dimensions of matrices and device resources; it is not a fixed delay. Reusing software assets reduces initialization traffic but does not replace a fresh study assessment with stored study scores.

For bulk assessment or matrices beyond browser limits, the [Docker application](https://github.com/BiosystemEngineeringLab-IITB/merit-ml) runs the shared Python calculation and report code natively on the user's computer. It retrieves inputs from the official repository endpoints and retains run files in its local data volume. Those local files can survive a container restart until the user removes the stored run data or volume. Docker does not impose the browser's WebAssembly heap or 50 MB transfer/output limits; execution still depends on the host's resources and configured task limits. [Bulk assessment and local use](https://merit-ml.in/live/manual/#17-bulk-assessment-and-local-docker-use) give instructions.

Assessment exports record the applied inputs and settings. Where the browser permits the output size, the run-record ZIP includes retained repository responses, completed outputs and execution/provenance records; omitted responses and interrupted checks remain documented rather than represented as complete inputs. These records distinguish changes caused by user settings from changes in the repository or software. The [complete metric checklist](https://merit-ml.in/live/manual/#20-complete-metric-and-evidence-checklist) links each result to its definition.

### 22.9 Links to every manual section

Use these links for the complete metric definitions and operating instructions.

- [1. Start here](https://merit-ml.in/live/manual/#1-start-here)
- [2. Studies, analyses, data files and denominators](https://merit-ml.in/live/manual/#2-studies-analyses-data-files-and-denominators)
- [3. Read the Overview](https://merit-ml.in/live/manual/#3-read-the-overview)
- [4. Choose the classification task](https://merit-ml.in/live/manual/#4-choose-the-classification-task)
- [5. Adjust samples, labels, batches and subjects](https://merit-ml.in/live/manual/#5-adjust-samples-labels-batches-and-subjects)
- [6. Structural diagnostics](https://merit-ml.in/live/manual/#6-structural-diagnostics)
- [7. Metadata and FAIR Reusability](https://merit-ml.in/live/manual/#7-metadata-and-fair-reusability)
- [8. Analytical QC: samples, controls and processing](https://merit-ml.in/live/manual/#8-analytical-qc-samples-controls-and-processing)
- [9. Analytical QC: the four alerted metrics](https://merit-ml.in/live/manual/#9-analytical-qc-the-four-alerted-metrics)
- [10. Analytical QC: scale, units and contextual evidence](https://merit-ml.in/live/manual/#10-analytical-qc-scale-units-and-contextual-evidence)
- [11. Annotation / Interoperability](https://merit-ml.in/live/manual/#11-annotation-interoperability)
- [12. Label Structure and Class Support metrics](https://merit-ml.in/live/manual/#12-label-structure-and-class-support-metrics)
- [13. ML Task Readiness metrics](https://merit-ml.in/live/manual/#13-ml-task-readiness-metrics)
- [14. All numerical controls](https://merit-ml.in/live/manual/#14-all-numerical-controls)
- [15. R1 and alert interpretation](https://merit-ml.in/live/manual/#15-r1-and-alert-interpretation)
- [16. Downloads and provenance](https://merit-ml.in/live/manual/#16-downloads-and-provenance)
- [17. Bulk assessment and local Docker use](https://merit-ml.in/live/manual/#17-bulk-assessment-and-local-docker-use)
- [18. Browser limits and partial results](https://merit-ml.in/live/manual/#18-browser-limits-and-partial-results)
- [19. Troubleshooting and practical interpretation](https://merit-ml.in/live/manual/#19-troubleshooting-and-practical-interpretation)
- [20. Complete metric and evidence checklist](https://merit-ml.in/live/manual/#20-complete-metric-and-evidence-checklist)
- [21. Getting help and keeping your results](https://merit-ml.in/live/manual/#21-getting-help-and-keeping-your-results)

## 23. Update analytical alert thresholds

**Optional; local Docker application only.** The prominent **Update thresholds** control opens an independent workflow for recalculating the comparison reference used by Missing entries and zeros, Low-information features, Redundant features and Outlier samples. In the browser edition it is blurred and disabled, with a link to Docker. Ordinary assessment does not require this operation. Original R1 remains the default until you approve another reference.

### 23.1 Select public release dates

Choose **From** and **Through** with the calendars. Both dates are included. Selection uses Workbench's public release date, rather than submission or revision date. The application retrieves the current public registry and release metadata, excludes future releases and studies not confirmed public, and lists missing or conflicting release dates separately. A fresh per-study analysis registry and summary are checked again during retrieval. A changed or unconfirmed release date excludes that study from calculation.

Choose either **Original R1 plus selected studies** or **Selected date range only**. The first retains the original 11,749 derived score rows and adds newly retrieved matrix identities once; an existing study–analysis–endpoint does not replace or duplicate its original R1 row. The second builds its reference solely from valid matrices in the selected range. The original R1 comparison cohort is the 9 March 2026 cohort represented in the 18 September deposit. Its small score-only baseline contains identifiers and four derived values per matrix, without sample identifiers or measurement tables. It is used for extending the reference, never as the assessment of a newly requested study.

After **Find public studies**, review the study count, analysis count and possible endpoints. Valid matrix counts cannot be known from the registry alone and become available after the tables are retrieved. Missing date information is not silently replaced by submission dates. Discovery can take several minutes when the repository responds slowly.

### 23.2 Calculate and follow progress

Click **Calculate thresholds**. One study is fetched and assessed at a time, using the same official endpoints and scientific functions as ordinary local assessment. Only the stages needed for the four analytical scores are run; class separability and annotation are not calculated for this operation. Calculations use the default analytical parameters and sample-eligibility rules, without your report's custom classes, fold setting, sample edits or alternative analytical cut-offs. The new reference must compare a consistent set of default scores.

The progress bar shows completed studies, the current study/check, valid matrices and failures. Each completed study has a durable compact result. **Pause after current study** stops after that result has been saved; **Resume calculation**, or the saved-run selector after reopening the application, continues without repeating completed studies. A process or machine interruption is reported separately from successful completion. A long cohort can take hours or days; keep the local application running while it calculates. Only one reference calculation runs at a time.

Raw responses, parsed matrices and large correlation/intermediate files occupy a temporary working directory for the current study. They are deleted after its compact result or failure has been saved. Retained records contain the four scores, matrix dimensions, study/analysis/endpoint identifiers, release dates, source URLs and response hashes/times, failures and reference provenance. They contain no retained measurement table. Disk space must still accommodate the largest study and its intermediate calculations temporarily. Saved reference history and compact provenance remain in the local application's data volume until you remove that data.

### 23.3 Review the proposed reference

The completed panel shows reference studies, analyses and matrices; counts by endpoint; the newly retrieved and original-baseline matrix counts; and each metric's assessable count, median and interquartile range. Compare the candidate's extreme and low cut-offs against original R1. The calculation uses the same linear-interpolation percentile rule as R1 and retains the 2.5th, 5th, 7.5th and 10th percentiles for all four checks. Missing or unassessable scores are omitted independently for each metric, rather than assigned zero. Ties at a cut-off do not raise an alert.

Download the candidate, score/provenance records and cohort manifest before approval if you want an independent check. Each records the selected dates and cohort mode, retrieval/calculation period, source/code fingerprints, counts and failures. When discovery, dates or individual calculations leave incomplete coverage, the panel explains it and requires explicit acknowledgement before approval. If any metric has no assessable score, no usable reference can be created. A reference based on very few matrices can have unstable percentiles; inspect the reported counts before deciding to use it.

### 23.4 Approve, apply or restore

**Approve and activate these thresholds** is a separate step. Calculation by itself never changes an assessment's alert thresholds. Approval verifies the reviewed candidate's digest and reproduces its cut-offs from its retained score rows, then atomically stores the active reference and a dated history entry in your local data volume. Original R1 is not overwritten. The report displays the active reference identifier, cohort/date description and matrix count; structured assessment settings identify the reference and its update provenance.

The approved reference supplies the percentile-based defaults to new reports and later recalculations, including Data at a glance. Reports open in another tab receive an update signal and refresh through the ordinary report form; if browser storage events are unavailable, use Apply or reopen the report. Existing explicit custom score cut-offs continue to take precedence. The percentile selectors still select among the four retained percentiles. Class definitions, sample counts, analytical scores and original measurement values do not change merely because a different reference is activated.

Use **Restore original R1** to return the percentile-based defaults to the original reference. Saved candidates and approval history are retained. This operation affects your local application only: it does not change the public website or anyone else's installation. To reproduce a result, save the assessment JSON and the reference/provenance downloads. The [alert interpretation](https://merit-ml.in/live/manual/#15-r1-and-alert-interpretation), [numerical controls](https://merit-ml.in/live/manual/#14-all-numerical-controls), [downloads](https://merit-ml.in/live/manual/#16-downloads-and-provenance) and [Docker instructions](https://merit-ml.in/live/manual/#17-bulk-assessment-and-local-docker-use) remain applicable.

### 23.5 Command-line use

The same workflow is available as `threshold_update.py` in the Docker image. From a running container named `merit-v2`, use `docker exec merit-v2 python threshold_update.py prepare --start-date YYYY-MM-DD --end-date YYYY-MM-DD --mode extend_r1`. The output includes a reference run ID. Use that ID with `docker exec merit-v2 python threshold_update.py run --id RUN_ID` to calculate, or with `status --id RUN_ID` to inspect progress. Use `--mode selected_range` when preparing a reference from only the chosen dates. The graphical review/approval page remains the recommended way to activate the result. Command-line approval requires the exact candidate SHA-256 and, when coverage is incomplete, `--accept-partial`. `docker exec merit-v2 python threshold_update.py restore` restores original R1. On Linux installations that require it, prefix Docker commands with `sudo`.
