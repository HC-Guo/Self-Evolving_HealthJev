# Stage4 physionet2012_sofa_severity verified-feedback SOP

Stored inside the cloned HealthJev repository under memory/L3_sops.
Induced only from previous completed L4 episodes in this prequential run.
The current case label is never available before prediction.

## Label Evidence From Prior L4 Episodes
- low: support_count=8; recent_cues=MAP min=74.0 mmHg is above the SOFA cardiovascular threshold (MAP <70 *despite vasopressors* or <65 without), and no other organ meets SOFA ≥2 criteria — 'low' severity requires ze | MAP min=60.0 mmHg alone does not satisfy SOFA cardiovascular criterion without evidence of vasopressor use or hypotension-induced organ hypoperfusion; verified 'low' severity requi | SOFA-based severity requires strict concurrent threshold violation across organ domains; isolated borderline values (e.g., platelet drop <30%, urine mean near but not sustained bel
- high: support_count=7; recent_cues=Platelet crash (>25% decline) + persistently elevated BUN/Creatinine + critical low MAP (min <70 mmHg and mean <70 mmHg across ≥40 readings) in a high-age medical ICU patient const | Sustained hypotension (MAP <70 mmHg, steep downward trend), profound neurologic impairment (GCS=3), and oliguria with elevated creatinine constitute definitive multimodal SOFA doma | MAP min = 59.0 mmHg is below 65 mmHg threshold, and critically, MAP first = 62.0 and last = 70.0 mask an early critical hypotensive episode — SOFA cardiovascular score requires onl
- moderate: support_count=5; recent_cues=MAP min=48.0 alone does not confirm cardiovascular SOFA ≥3 without evidence of vasopressor use or persistent hypotension unresponsive to fluids; verified 'moderate' reflects isolat | SOFA organ failure requires *concurrent* dysfunction across ≥2 domains *at the same time point*—not just independent threshold breaches across different times; MAP min=56 satisfies | Urine min=35 mL is insufficient to confirm SOFA renal dysfunction without duration context; SOFA requires <0.5 mL/kg/h for ≥2 consecutive hours — but sparse urine sampling (count=4

## Recurrent Confusion Pairs
- high -> moderate: 5
- high -> low: 4
- moderate -> low: 2
- low -> high: 1

## Use Rule
Use this SOP as a checking guide. It must not override current visible clinical evidence.
