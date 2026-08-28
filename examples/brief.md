Title: Retrieval-augmented triage of radiology reports

We fine-tuned a small open-weight LLM to triage free-text radiology reports into four
urgency classes, augmenting each prediction with retrieved prior reports for the same
patient. Dataset: 41,000 de-identified reports from two hospitals, 2019-2023.
Baselines: BM25+logistic regression, a zero-shot LLM, and the same model without retrieval.
Headline result: macro-F1 0.81 vs 0.74 without retrieval; the gain concentrates in the
two rarest classes. Known weaknesses: single-vendor imaging at site B, no prospective
evaluation, retrieval helps much less when the patient has no prior study.
Audience: a clinical informatics venue. Emphasise the ablation and the failure analysis.
