# ReadinessScore Distribution Across the Repository

- Workflow files parsed: **3945**
- Analysis rows: **11623**
- Unique studies represented: **3945**

## 1) Overall Readiness Band Distribution

| band        | count | percent |
| ----------- | ----- | ------- |
| Ready       | 4175  | 35.92   |
| Conditional | 4202  | 36.15   |
| Fragile     | 35    | 0.30    |
| Not Ready   | 3211  | 27.63   |
| No Data     | 0     | 0.00    |

## 2) Source-Stratified Breakdown

### Score Summary by Source

| source      | n_rows | n_studies | mean_score | median_score | std_score |
| ----------- | ------ | --------- | ---------- | ------------ | --------- |
| datatable   | 4819   | 3100      | 0.8853     | 0.8950       | 0.0670    |
| mwtab       | 4939   | 3187      | 0.8752     | 0.8860       | 0.0718    |
| untarg_data | 1865   | 983       | 0.7844     | 0.7990       | 0.0791    |

### Dimensions Driving Source Differences (ranked by spread)

| dimension      | max_source  | max_mean_score | min_source  | min_mean_score | spread_max_minus_min | datatable_minus_mwtab | datatable_minus_untarg_data | mwtab_minus_untarg_data |
| -------------- | ----------- | -------------- | ----------- | -------------- | -------------------- | --------------------- | --------------------------- | ----------------------- |
| annotation     | datatable   | 0.9791         | untarg_data | 0.5804         | 0.3986               | 0.0108                | 0.3986                      | 0.3879                  |
| metadata       | datatable   | 0.6084         | untarg_data | 0.2773         | 0.3311               | 0.0110                | 0.3311                      | 0.3201                  |
| ml_feasibility | datatable   | 0.9143         | untarg_data | 0.8546         | 0.0596               | 0.0061                | 0.0596                      | 0.0536                  |
| analytical     | datatable   | 0.9027         | untarg_data | 0.8463         | 0.0564               | 0.0341                | 0.0564                      | 0.0224                  |
| structural     | untarg_data | 0.9763         | mwtab       | 0.9658         | 0.0105               | 0.0017                | -0.0089                     | -0.0105                 |
| cohort         | mwtab       | 0.6650         | datatable   | 0.6628         | 0.0022               | -0.0022               | -0.0014                     | 0.0008                  |

## 3) Study-Level Aggregation and Spread

- Studies with >1 analysis: **1663**

### Top 5 Studies by Within-Study Score Range

| study_id | n_unique_analyses | n_sources | n_platforms | score_min | score_max | score_range | n_distinct_bands |
| -------- | ----------------- | --------- | ----------- | --------- | --------- | ----------- | ---------------- |
| ST002132 | 1                 | 2         | 1           | 0.5300    | 0.9820    | 0.4520      | 2                |
| ST002866 | 2                 | 3         | 1           | 0.5920    | 0.9470    | 0.3550      | 2                |
| ST003477 | 1                 | 2         | 1           | 0.5280    | 0.8370    | 0.3090      | 1                |
| ST000255 | 1                 | 2         | 1           | 0.6920    | 0.9870    | 0.2950      | 2                |
| ST001422 | 2                 | 3         | 1           | 0.6720    | 0.9600    | 0.2880      | 2                |

### Platform vs Band Association

| n_rows_used | n_platform_buckets | n_bands | cramers_v |
| ----------- | ------------------ | ------- | --------- |
| 11623       | 3                  | 5       | 0.0117    |

## 4) Dimension-Wise Bottleneck

- Universal lowest-scoring dimension (mean): **metadata** (mean score **0.5505**)

## 5) Score Confidence Distribution

| confidence | count | percent |
| ---------- | ----- | ------- |
| Low        | 1963  | 16.89   |
| Moderate   | 8497  | 73.11   |
| High       | 1163  | 10.01   |
