# Data Ingestion Methodology for Public Metabolomics Repository Archival

## Systematic Acquisition of the MetaboLights and Metabolomics Workbench Corpora

**Shayantan Banerjee**

*Section prepared for: MERIT — Machine Learning Readiness Framework for Tabular Metabolomics Data*

---

## Overview

Systematic machine learning readiness assessment at repository scale requires a complete, locally archived copy of the target databases. Two major public metabolomics repositories were selected for comprehensive archival: MetaboLights, hosted by the European Bioinformatics Institute (EBI), and Metabolomics Workbench, maintained by the National Metabolomics Data Repository (NMDR) at the University of California San Diego. The data ingestion strategy for each repository was designed independently, reflecting the distinct data models, access protocols, and file formats native to each resource. Together, these archives constitute the foundational data substrate for all downstream readiness assessments performed by MERIT.

This document describes the complete data ingestion methodology for both repositories, covering source identification, format characterization, technical access protocols, file acquisition logic, provenance recording, and the resulting archive structure. The description is organized to support full reproduction of the ingestion process and to contextualize design decisions made in response to repository-specific constraints.

---

## Part I — MetaboLights Data Ingestion

### 1.1 Repository Overview and Data Access Strategy

MetaboLights is an open-access, database-independent repository for metabolomics experiments maintained by the European Bioinformatics Institute as part of the EMBL-EBI infrastructure. Each study in MetaboLights is assigned a unique accession identifier following the pattern MTBLS followed by a numeric suffix. Deposited studies conform to the ISA-Tab metadata standard, which structures experimental metadata across three interlocking file types: Investigation files, Study files, and Assay files. Abundance data is deposited as Metabolite Annotation Format (MAF) tables, which are tab-separated files encoding per-sample metabolite intensity measurements alongside chemical annotation columns.

The primary access route for large-scale programmatic retrieval of MetaboLights data is the EBI File Transfer Protocol (FTP) server, accessible at ftp.ebi.ac.uk under the path /pub/databases/metabolights/studies/public. Each study directory contains all ISA-Tab metadata files and MAF abundance tables associated with that accession. However, direct FTP enumeration of the full study list requires iterating through individual study directories, which is rate-limited and impractical at scale. A more efficient alternative was identified: EBI publishes machine-readable XML database dumps, formatted according to the European Bioinformatics Institute Search (EBI Search) schema, which encode metadata for all public studies in a single consolidated file. These XML dumps are made available on the EBI FTP server at ftp.ebi.ac.uk/pub/databases/metagenomics/ebi_search/ and are regenerated periodically as the repository is updated.

Two XML dump files were identified as relevant: a complete database export encoding all MetaboLights entries (including both study records and compound records), and a studies-only export encoding exclusively experimental study accessions. Given that the analysis objective was study-level readiness assessment, the studies-only export was selected as the primary ingestion source.

### 1.2 XML Corpus Characterization

The MetaboLights studies XML dump, retrieved on 7 March 2026 (release version 4), was a 372-megabyte file containing 2,712 study records. Each record in the XML corresponds to one MTBLS accession and is encoded as a structured entry element carrying a study identifier attribute and a set of named sub-elements. The top-level entry structure includes a human-readable study name, a free-text description constituting the study abstract, a set of cross-reference links to external databases (including HMDB, LIPID MAPS, and RefMet), structured date elements recording submission and publication timestamps, and an extensible additional fields block containing up to 117 distinct named metadata fields per entry.

The additional fields block was found to encode the richest source of study-level information. Among the fields identified were organism (the taxonomic name of the primary study species), organism_part (the biological tissue or sample matrix), sample_collection_protocol (a narrative description of sample recruitment and collection procedures), study_design (one or more experimental design keywords curated by submitters), and curator_keywords (a structured set of controlled vocabulary terms applied by MetaboLights curators). Additional fields captured platform information (technology type and instrument platform), protocol narratives for extraction, chromatographic separation, mass spectrometry acquisition, data transformation, and metabolite identification, and dataset file links pointing to individual files available for download from the EBI FTP server.

A critical finding during XML characterization was that a dedicated disease field is present in the schema but is systematically empty across the vast majority of deposited studies. Disease-relevant information must therefore be extracted indirectly from free-text fields including the study description, sample collection protocol narrative, study design keywords, and curator keyword lists. This observation directly motivated the multi-source disease extraction strategy described in Section 1.5.

The complete XML schema was documented by parsing all 2,712 entries and enumerating every field name encountered across the corpus, yielding an inventory of 117 unique field types with their frequency of occurrence. Cross-reference databases included HMDB (Human Metabolome Database), LIPID MAPS, MetaboLights compound accessions, ChEBI, and PubChem. The dataset file links field pointed to direct file paths on the EBI FTP server and HTTPS mirror, making it possible to construct download URLs for individual study files programmatically from within the XML without additional API calls.

### 1.3 Study File Acquisition

The file acquisition pipeline operated by scanning each entry in the XML for dataset file link elements, filtering the identified URLs against a regular expression pattern that matched the four ISA-Tab and MAF file types characteristic of MetaboLights deposits. Specifically, files whose names matched the following patterns were targeted: Metabolite Annotation Format tables (files beginning with m_ and ending with _maf.tsv), Assay description files (files beginning with a_ and ending with .txt), Investigation files (files beginning with i_ and ending with .txt), and Study sample description files (files beginning with s_ and ending with .txt).

For each matching file URL identified in the XML, the pipeline constructed a local output path within a hierarchical directory structure rooted at a designated data dump directory. Files were organized by study accession: each MTBLS accession received its own subdirectory, within which all associated files were placed without further nesting. This flat-within-study organization mirrors the file layout used on the EBI FTP server itself, simplifying cross-referencing between the local archive and the upstream source.

URL normalization was applied prior to each download request to handle percent-encoding inconsistencies observed in some dataset file link values. The normalization procedure applied standard URI encoding rules to path components and query strings, producing a canonical URL form for each target file. Where an FTP-scheme URL was identified (ftp://ftp.ebi.ac.uk/...), an HTTPS equivalent was constructed by substituting the HTTPS mirror endpoint (https://ftp.ebi.ac.uk/...) and attempted first, with fallback to the original FTP URL if the HTTPS retrieval failed. This dual-candidate strategy reduced download failures caused by protocol-specific network restrictions.

Each file download was attempted with a configurable retry mechanism. On transient failures (network timeouts, temporary server errors), the pipeline applied exponential backoff, waiting 2 seconds before the first retry and doubling the wait on each subsequent attempt up to a ceiling of 30 seconds, for a maximum of three total attempts per file. Files that failed all retry attempts were logged with their full error details and skipped without halting the overall pipeline. A temporary partial-file mechanism prevented incomplete downloads from masquerading as successfully retrieved files: each download was first written to a path with a .part suffix and only renamed to the final target path upon confirmed successful completion.

Files already present on disk with a non-zero size were unconditionally skipped, enabling interrupted runs to resume without re-downloading previously acquired content. This skip-on-exists behavior was implemented as the first check within the per-file download logic, making the pipeline fully idempotent with respect to previously successful downloads.

### 1.4 Checkpoint and Progress Logging

The acquisition pipeline maintained two persistent state files to support resumable operation across interruptions. A checkpoint file, stored in JSON format, recorded the index of the most recently processed download item, the total number of planned items for the current run, a per-status summary (downloaded, skipped, failed), and the UTC timestamp of the most recent checkpoint write. This file was updated after each file was processed, ensuring that any interruption would lose at most one file unit of progress.

A supplementary progress log, stored in newline-delimited JSON format, appended one record per processed file containing the full metadata of each download outcome: the study accession, the filename, the resolved source URL, the local destination path, the outcome status, the file size in bytes, and any error message in the case of failure. Additionally, two structured records marking the start and end of each pipeline run were appended to the progress log, providing a complete execution timeline. A conventional log file in human-readable timestamped format was written in parallel, directed to both disk and standard output, to support interactive monitoring of pipeline execution.

### 1.5 Disease Annotation Extraction from MetaboLights Metadata

Given the systematic absence of structured disease labels in the dedicated disease field of the MetaboLights XML, a multi-source pattern-matching strategy was developed to extract disease annotations from free-text metadata fields. This extraction was performed as a post-processing step over the XML corpus, operating on the five text fields most likely to contain disease-relevant content: the study title, the free-text description, the study design keywords, the curator keyword list, and the sample collection protocol narrative.

A curated library of regular expression patterns was constructed to identify mentions of 60 distinct disease entities across nine major clinical domains. The cancer domain received the most granular coverage, with individual patterns for breast cancer, lung cancer, colorectal cancer, prostate cancer, hepatocellular carcinoma, pancreatic cancer, ovarian cancer, gastric cancer, bladder cancer, melanoma, glioblastoma, neuroblastoma, leukemia (with explicit subtypes including acute myeloid leukemia, acute lymphoblastic leukemia, chronic myeloid leukemia, and chronic lymphocytic leukemia), lymphoma, and a general cancer fallback pattern. Metabolic diseases covered included type 2 diabetes mellitus, type 1 diabetes mellitus, a general diabetes pattern, obesity and adiposity, metabolic syndrome, insulin resistance, and non-alcoholic fatty liver disease with its clinical stages. Cardiovascular diseases covered included coronary artery disease, myocardial infarction, heart failure, atherosclerosis, hypertension, and stroke. Neurological diseases included Alzheimer disease, Parkinson disease, multiple sclerosis, epilepsy, Huntington disease, and dementia. Autoimmune and inflammatory diseases included inflammatory bowel disease (with explicit recognition of Crohn disease and ulcerative colitis), rheumatoid arthritis, psoriasis, systemic lupus erythematosus, and celiac disease. Infectious diseases included COVID-19 and SARS-CoV-2, malaria, tuberculosis, sepsis, general infection, and hepatitis. Kidney diseases covered chronic kidney disease and nephropathy. Respiratory diseases covered asthma, chronic obstructive pulmonary disease, and cystic fibrosis. Mental health conditions included major depressive disorder, schizophrenia, bipolar disorder, and generalized anxiety disorder. Additional entities included preeclampsia, endometriosis, polycystic ovary syndrome, osteoporosis, anaemia, sickle cell disease, and Down syndrome.

Pattern specificity was carefully tuned to minimize false positive extractions. Critically, patterns for disease subtypes were designed to require the disease name itself rather than more generic contextual words. For example, the leukemia pattern required explicit disease name strings rather than abbreviated clinical acronyms that appear commonly in metabolomics study designs with different meanings. Similarly, the glioma pattern was restricted to glioma and glioblastoma and did not include broader phrases such as brain tumor, since neuroblastoma studies and other brain-related metabolomics work would otherwise be misclassified. Each pattern was anchored with word boundary markers to prevent partial-word matches from triggering false extractions.

Extraction was performed independently over each of the five text sources per study, yielding a source-tagged dictionary of identified disease terms. A multi-source confidence scoring system was applied: studies in which a given disease term was independently identified in three or more distinct text fields received a high confidence rating; identification in two sources received a medium confidence rating; identification in a single source received a low confidence rating. This approach rewards convergent evidence and allows downstream filtering by confidence level.

A normalization step was applied to remove redundant parent-class terms when more specific disease terms had already been identified. Specifically, general terms such as cancer, cancer_general, diabetes_general, and cardiovascular_disease were suppressed from the extracted disease list when one or more specific subtypes (for example, breast_cancer, type_2_diabetes, or myocardial_infarction) had been identified in the same study. This prevents double-counting and ensures that the most informative disease label is retained rather than the most generic one.

Organism filtering was applied prior to disease extraction to restrict the analysis to human studies. An entry was designated as a human study if the organism field contained either Homo sapiens or the term human in any case combination. Only studies passing this filter were subjected to disease extraction and included in the extracted disease study outputs.

### 1.6 Human Study Descriptor Extraction

In parallel with disease annotation extraction, a comprehensive descriptor extraction pass was performed over all human studies to generate a structured tabular resource for downstream use. For each human MetaboLights study, the following fields were extracted and consolidated: the study accession identifier, the study title, the full free-text description, the organism name, the organism part (sample matrix), the sample collection protocol narrative, the concatenated study design keywords (semicolon-delimited where multiple values were present), and the concatenated curator keywords (semicolon-delimited). These fields were selected to collectively represent the study's scientific context, specimen characteristics, and methodological framing in a form suitable for text mining, natural language processing, or manual review.

The extraction was performed using a streaming XML parsing approach (iterative element parsing rather than full document loading into memory) to accommodate the 372-megabyte XML file without exhausting system memory. Each XML entry element was processed as it was encountered during streaming, the relevant field values were extracted, and the element was immediately discarded from memory. This approach maintained a near-constant memory footprint regardless of XML file size.

The resulting human study descriptor table contained 685 records, corresponding to the subset of the 2,712 total MetaboLights studies in which the organism field designated a human origin. All 685 records had 100% field population for all five descriptor fields (title, description, organism, sample protocol, study design, and curator keywords), indicating that these core metadata elements are consistently curated across the MetaboLights corpus for human studies. The most frequently represented sample matrices were blood plasma (122 studies), blood serum (86 studies), feces (52 studies), urine (40 studies), and saliva (14 studies).

### 1.7 Archive Completeness and Validation

Upon completion of the download pipeline, the MetaboLights local archive contained 2,712 study subdirectories, exactly matching the count of MTBLS study records present in the XML. File-type counts within the archive were as follows: 4,866 Metabolite Annotation Format tables, 5,604 Assay description files, 2,710 Investigation files, and 2,719 Study sample description files. The higher counts for MAF and Assay files compared to the number of studies reflect the common practice of depositing multiple assay instances per study (for example, positive and negative ion mode acquisitions, or GC-MS and LC-MS platforms used in parallel on the same samples). Investigation file and study sample file counts were close to, but not exactly, 2,712, reflecting a small number of studies with non-standard file naming that caused them to not match the inclusion filter pattern during the XML file link scan.

---

## Part II — Metabolomics Workbench Data Ingestion

### 2.1 Repository Overview and API Architecture

The Metabolomics Workbench is a public metabolomics data repository operated by the National Center for Metabolomics Data Repository (MCMDR), hosted at the University of California San Diego and funded by the National Institutes of Health Common Fund. Studies in the Metabolomics Workbench are assigned study identifiers of the form ST followed by a six-digit zero-padded number (for example, ST000001). Unlike MetaboLights, which uses the ISA-Tab standard for metadata and MAF for abundance data, the Metabolomics Workbench organizes data around a two-level hierarchy of studies and analyses. Each study may contain one or more independent analytical runs, each assigned its own analysis identifier (AN followed by a six-digit number). A single study may thus produce abundance matrices at the individual analysis level, where each analysis represents a distinct chromatographic or spectroscopic experiment (for example, untargeted positive mode LC-MS, untargeted negative mode LC-MS, and targeted GC-MS panels analyzed on the same biological specimens).

Programmatic access to Metabolomics Workbench data is provided through a Representational State Transfer application programming interface (REST API) hosted at metabolomicsworkbench.org/rest. The API exposes endpoints for study enumeration, per-study metadata retrieval, per-analysis metadata retrieval, and per-analysis data table download. Authentication is not required for any of the data retrieval endpoints, making fully automated acquisition straightforward. All API responses are returned in JSON format.

### 2.2 Study Enumeration via the Summary Endpoint

The complete list of publicly available studies was retrieved from the study summary endpoint, which returns a JSON object keyed by sequential integer indices. Each value in the response is a nested object containing study-level metadata including the study identifier, study title, principal investigator, institute affiliation, study type, collection method, species, tissue, disease, number of subjects, and number of samples. The total number of studies returned by this endpoint at the time of initial enumeration (8 March 2026) was 4,121. Study identifiers were extracted from the response by iterating over all values in the JSON object, filtering for entries that contained a study identifier field, and collecting all values that matched the expected ST-prefixed identifier pattern. The resulting sorted list of 4,121 study identifiers served as the enumeration basis for all subsequent per-study download operations.

### 2.3 Per-Study Analysis Enumeration

For each study in the enumerated list, the set of associated analyses was retrieved from the per-study analysis endpoint. This endpoint returns the analysis metadata for a given study, which may take one of three JSON structural forms depending on the number of analyses present: a single analysis encoded directly as a flat JSON object, a list of analysis objects, or a nested dictionary keyed by sequential integers where each value is an analysis object. The ingestion pipeline handled all three structural forms, normalizing the response to a flat list of analysis records regardless of the source structure. Each analysis record was required to carry a valid analysis identifier field; records without an analysis identifier were discarded.

### 2.4 Tabular Data Acquisition: Priority and Format Handling

For each analysis, tabular metabolite abundance data was sought from three distinct sources, accessed in priority order. The choice of priority reflects both data quality and availability considerations.

**Primary source — Datatable endpoint:** The Metabolomics Workbench REST API exposes a datatable endpoint that returns the quantitative abundance matrix for a given analysis identifier. The response is a tab-separated file in which features (metabolites) are organized as rows and samples appear as columns, with the first column containing feature identifiers. Downloaded datatable files were written to disk under the analysis-specific tabular subdirectory and verified for content validity. Verification consisted of reading the downloaded file and confirming that it contained more than one row (excluding the header) and that the header row contained more than two columns (the minimum for a meaningful matrix with at least one sample beyond the identifier column). Files that failed content verification were deleted. The majority of datatables were received as plain-text tab-separated files, though a subset were transmitted with gzip compression. Both formats were handled during verification by first attempting to decompress the file and falling back to plain-text reading if decompression failed, with the file renamed to remove the compressed file extension in the latter case.

**Secondary source — Results.txt download page:** For analyses where the datatable endpoint returned no usable data, the study download page — a standard HTML page served from the Metabolomics Workbench web interface — was parsed to locate downloadable result files. This page lists files available for a given study, with hyperlinks pointing to individual file downloads identified by URLs containing a study download path component. The HTML was parsed by extracting all anchor element href attributes that matched the study download URL pattern, retaining those whose associated link text ended in Results.txt. Link text was used to extract the analysis identifier from the filename (by searching for an AN-prefixed identifier pattern within the filename string). Files identified on the download page as belonging to the current analysis were downloaded and verified using the same content validation procedure applied to datatable files.

**Tertiary source — mwtab format files:** Independent of whether tabular data was successfully obtained, mwtab-formatted files were retrieved for every analysis from the dedicated mwtab API endpoint. The mwtab format is a structured plain-text format native to the Metabolomics Workbench that encodes both metadata and tabular data in a single file using fixed-format section headers. mwtab files were downloaded in both JSON and plain-text variants. These files serve as the primary metadata carrier for analyses — providing details on the analytical platform, instrument parameters, sample preparation protocols, and metabolite identification methods — and are stored in a separate JSON subdirectory alongside the tabular data.

### 2.5 Directory Organization and Per-Study Manifest

The local archive was organized as a two-level directory hierarchy. The top-level directory contained one subdirectory per study, named by the study identifier. Within each study directory, one subdirectory was created per analysis, named by the analysis identifier. Within each analysis directory, two further subdirectories organized the file types: a tabular subdirectory for abundance data files (datatable files and Results.txt files) and a JSON subdirectory for metadata files (mwtab JSON and mwtab plain-text files).

Upon completion of all download attempts for a study, a machine-readable manifest file was written to the study directory in JSON format. The manifest recorded the study identifier, the outcome status (whether any analysis contained usable tabular data), any error encountered during processing, and a per-analysis breakdown listing the local relative paths of all successfully retrieved files for each analysis type, along with a Boolean indicator of whether tabular abundance data was successfully obtained for that analysis.

### 2.6 Progress Tracking, Fault Tolerance, and Resume Capability

Given the scale of the download operation (4,121 studies, 6,696 analyses), the ingestion pipeline was designed for robustness against network interruptions, server-side transient errors, and partial session terminations. All HTTP requests were issued with a configurable retry policy: each request was attempted up to three times, with exponential backoff (2 seconds before the first retry, 4 seconds before the second) applied between attempts. HTTP 404 responses were treated as definitive failures and not retried. Other HTTP error codes (5xx server errors) and network-level exceptions (connection timeouts, connection resets) triggered retry logic.

A one-second inter-study pause was inserted after each completed study to avoid placing excessive request load on the Metabolomics Workbench servers during extended download sessions, consistent with responsible use of public infrastructure.

Progress state was persisted to a JSON file after each study was processed, recording three disjoint lists: studies successfully downloaded with tabular data, studies where no tabular data was available in any analysis, and studies that failed with an error, along with the associated error message. The total study count and a last-updated timestamp were also written. This progress file enabled the pipeline to resume from any interruption without reprocessing previously handled studies: on startup, the pipeline loaded the progress file, computed the set of studies not yet appearing in any outcome list, and restricted processing to that remaining set. The resume behavior was fully transparent — no user intervention was required beyond restarting the pipeline.

### 2.7 Session Logging and Execution Timeline

Detailed timestamped logs were written to a rotating log file named with the session start timestamp. Each log entry captured the study identifier and ordinal position, the analysis identifier being processed, the download outcome for each file type, file sizes, per-file and per-study elapsed times, and any warning or error conditions encountered. A console-directed summary was produced in parallel at INFO level for interactive monitoring. The log file served as the primary audit record for the download session and was used retrospectively to diagnose the progress tracking discrepancy described below.

### 2.8 Progress Tracking Discrepancy and Recovery

An important operational issue arose during the first download session, in which approximately 1,598 studies (ST000001 through ST001798) were downloaded during an initial run spanning approximately 13 hours. Upon examining the progress JSON file after this session, it was found to contain only 8 completed study records rather than the expected 1,598. Investigation of the log file confirmed that all 1,598 studies had been processed successfully — the log contained explicit success entries for each study — but a defect in the progress file writing logic caused the in-memory completed study list to not be fully persisted.

The root cause was a progress counter increment bug in the display logic that caused the displayed ordinal position to count skipped (already-completed) studies and newly downloaded studies separately, resulting in an artificially inflated counter value. Critically, the completed study list was being appended correctly within the session but was not fully serialized to the JSON file after the session was interrupted, likely because the serialization occurred at fixed checkpoints rather than after every individual study append.

Recovery was performed by reconstructing the completed study list from the filesystem: all subdirectories matching the study identifier pattern in the download root were enumerated, sorted lexicographically, and used as the authoritative record of completed downloads. This reconstructed list of 1,598 entries was written to a new progress file, replacing the corrupted version. When the download pipeline was subsequently restarted, it correctly identified all 1,598 directories as already completed and resumed processing from study ST001799, downloading the remaining studies without re-downloading any previously acquired data.

### 2.9 Final Archive Completeness and Statistics

Upon completion of the full download pipeline (9 March 2026), the Metabolomics Workbench local archive contained 4,121 study directories. A cross-tabulation of directory counts against the progress file confirmed that 4,083 studies were recorded as successfully completed with tabular data, 38 studies were recorded as completed but without accessible tabular data in any analysis, and zero studies were recorded as having failed with an error. The absence of download failures reflects the robustness of the retry logic and the completeness of the Metabolomics Workbench API at the time of retrieval.

File-type counts within the archive were as follows: 4,870 datatable files in tab-separated format, 1,555 Results.txt files from the HTML download page, 6,688 mwtab JSON files, and 6,686 mwtab plain-text files. The aggregate 6,696 analysis directories across all 4,121 studies indicates an average of approximately 1.63 analyses per study, with a range from 1 (the majority of studies) to 8 (the highest observed for any single study in the archive). The disparity between the number of datatable files and the total analysis count reflects the 38 studies with no accessible tabular data, as well as individual analyses where the datatable endpoint returned no valid content and Results.txt served as the fallback.

---

## Part III — Integrated Archive Summary

### 3.1 Repository Coverage

**Note:** The current MERIT analysis focuses exclusively on Metabolomics Workbench. The MetaboLights archive (Part I) is retained for future cross-repository comparison but is not used in the 4,121-study readiness assessment.

The Metabolomics Workbench local archive contains 4,121 studies encompassing 6,696 independent analytical runs, representing the near-totality of publicly available MW data as of March 2026. The MetaboLights archive contains 2,712 studies (685 human-origin), available for future extension.

### 3.2 MW Data Format Landscape

The MW archive contains files in three primary formats:

- **Datatable / TSV:** Tab-separated abundance tables retrieved from the Metabolomics Workbench REST API, with features as rows and samples as columns. Feature identifiers vary by study type: named metabolite names for targeted studies, and mass-to-charge/retention-time strings for untargeted studies.

- **Results.txt:** Tab-separated files in either row-oriented (samples as rows, features as columns) or column-oriented (features as rows, samples as columns) layout, with a Factors row or column encoding class label information directly within the abundance table.

- **mwtab:** A plain-text format using fixed section headers to delimit blocks of metadata and tabular data within a single file. Available in both original plain-text and JSON-serialized variants. Encodes study-level metadata (subject characteristics, collection and analysis protocols), per-sample metadata, and the abundance matrix.

### 3.3 MW-Specific Supplementary Caches

In addition to the per-analysis tabular data, three study-level JSON caches are materialized for each MW study (always present, either populated or empty `[]`/`{}`):

- **`disease.json`** — cached study-level disease payload from the MW REST disease endpoint. Source-of-truth for `StudyRecord.disease`. 2,469 studies have non-empty disease records.
- **`factors.json`** — cached sample-level factors payload (class labels + sample source). First-class input for class label assignment and `sample_type`/`organism_part` propagation.
- **`metabolites.json`** — cached study metabolite payload. Used for RefMet evidence in `FairMetaboliteIdentifierResolvabilityMetric` (explicit evidence only; no dictionary fallback).

### 3.4 Relevance to Downstream Assessment

The local archive design was specifically optimized to support the MERIT readiness assessment pipeline. The two-level study/analysis hierarchy of the Metabolomics Workbench archive maps directly onto the canonical data model used by MERIT, where each analysis record is represented as a distinct feature matrix within the study bundle. The co-location of metadata files (mwtab JSON) with abundance data within the same study directory enables MERIT's connector to resolve both data types from a single directory traversal.

The manifest files written per study serve as pre-computed indexes that accelerate study loading: the connector reads the manifest to identify which analyses have usable tabular data and which file paths to load. The batch execution pipeline (`mw_full_run.py`) discovers all ST* directories and processes them sequentially, producing 8 JSON artifacts per study for a total precomputed cache that enables the UI to replay any study without re-computation.

---

## Appendix: Key Quantitative Summary

| Parameter | Metabolomics Workbench | MetaboLights (archived, not assessed) |
|---|---|---|
| Total studies archived | 4,121 | 2,712 |
| Studies with tabular data | 4,083 | ~2,712 (ISA-Tab) |
| Human studies identified | Not filtered (all species) | 685 |
| Total analyses | 6,696 | ~5,604 (assay files) |
| Abundance tables | 4,870 datatables + 1,555 Results.txt | 4,866 MAF files |
| Metadata files | 6,688 mwtab JSON + 6,686 mwtab TXT | 2,710 investigation + 2,719 sample |
| Supplementary caches | disease.json + factors.json + metabolites.json per study | N/A |
| Studies assessed by MERIT | **4,121** (full profile) | 0 (disabled) |
| Download period | 8–9 March 2026 | 7 March 2026 |
| Total archive size (approx.) | ~120 GB | ~50 GB |
| Primary access method | REST API (JSON) | EBI FTP / HTTPS XML dump |
| Resume capability | Progress JSON (study-level) | Checkpoint JSON (file-level) |
| Retry policy | 3 attempts, exponential backoff | 3 attempts, exponential backoff |

---

*This data ingestion documentation is part of the MERIT framework manuscript supplement. All pipeline code, configuration files, progress logs, and archive manifests are retained in the project repository under mw-dump-latest/ and metabolights-dump-latest/ respectively, enabling full reproduction of the ingestion process.*
