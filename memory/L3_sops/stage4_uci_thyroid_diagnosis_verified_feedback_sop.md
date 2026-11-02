# Stage4 uci_thyroid_diagnosis verified-feedback SOP

Stored inside the cloned HealthJev repository under memory/L3_sops.
Induced only from previous completed L4 episodes in this prequential run.
The current case label is never available before prediction.

## Label Evidence From Prior L4 Episodes
- normal: support_count=10; recent_cues=TSH_scaled = 0.00077 is *truly low* (not high) in UCI thyroid scaling — the inverse-log transform preserves directionality; ultra-low TSH with low peripheral hormones indicates cen | Isolated TSH suppression with low-to-mid peripheral thyroid hormones in a 'sick'=1 patient strongly indicates non-thyroidal illness (NTI), not hyperthyroidism — axis incoherence ov | Suppressed TSH_scaled alone is insufficient for hyperthyroid diagnosis when peripheral markers (TT4, FTI) fall within mid-range and lack coherent elevation—axis consistency require
- hypothyroid: support_count=6; recent_cues=In UCI thyroid data, low TSH_scaled values correspond to *high* absolute TSH due to inverse-log scaling; always cross-validate with peripheral hormone scaled values (T3, TT4, FTI)  | In UCI thyroid data, low TSH_scaled values correspond to *high* absolute TSH due to inverse-log scaling; always interpret scaled TSH in conjunction with scaled T3, TT4, and FTI to  | In UCI thyroid data, low scaled TSH values indicate *high* absolute TSH due to inverse-log scaling — always interpret scaled TSH inversely when assessing primary hypothyroidism.
- hyperthyroid: support_count=4; recent_cues=Suppressed TSH_scaled must be interpreted in concert with *consistently elevated* peripheral thyroid hormones (T3, TT4, T4U, FTI) — all four scaled values >0.12 indicate hyperthyro | Suppressed TSH (TSH_scaled < 0.05) combined with elevated TT4, T4U, and FTI — even with mid-range T3 — is pathognomonic for hyperthyroidism per thyroid axis physiology; isolated T3 | TSH_scaled values near 0.0072 in UCI thyroid data correspond to *low* absolute TSH (not high) due to inverse-log scaling — always cross-validate with peripheral hormone patterns: e

## Recurrent Confusion Pairs
- hyperthyroid -> normal: 4
- normal -> hyperthyroid: 2
- hypothyroid -> normal: 2
- normal -> hypothyroid: 1
- hypothyroid -> hyperthyroid: 1

## Use Rule
Use this SOP as a checking guide. It must not override current visible clinical evidence.
