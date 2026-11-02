# Stage4 physionet2012_sofa_severity HealthJev + tools verified-feedback SOP

Induced only from previous completed L4 episodes in this prequential run.
The current case label is never available before prediction. Tool evidence must be derived from visible input only.

## Label Evidence From Prior L4 Episodes
- low: support_count=8; recent_tool_memory_cues=Verified feedback: predicted=high, verified=low, outcome=incorrect, tool_helpfulness=harmful. | SOFA neurologic score requires GCS ≤8 for score=3; GCS=8 is borderline and must be interpreted with context—here, rapid improvement (GCS 8→15) and absence of other organ failure in | Isolated SOFA organ score of 2 (e.g., renal from Urine_min=0.0) does not elevate severity to 'high'; 'high' requires either ≥2 organs with score ≥2 or any single organ with score ≥
- high: support_count=7; recent_tool_memory_cues=Prioritize absolute SOFA threshold breaches over trends when organ dysfunction is definitively present (e.g., Cr ≥ 2.0 → renal SOFA ≥ 3), as single-organ score ≥3 alone suffices fo | Absolute SOFA threshold breaches (e.g., MAP ≤ 65, GCS ≤ 8, Creatinine ≥ 2.0 with anuria) are sufficient and decisive for 'high' severity — trend evidence reinforces but does not ov | MAP_first < 65 is a definitive binary trigger for 'high' SOFA severity, irrespective of trend or later improvement — absolute threshold breach overrides compensatory trends.
- moderate: support_count=5; recent_tool_memory_cues=Verified feedback: predicted=high, verified=moderate, outcome=incorrect, tool_helpfulness=neutral. | Verified feedback: predicted=high, verified=moderate, outcome=incorrect, tool_helpfulness=harmful. | Verified feedback: predicted=high, verified=moderate, outcome=incorrect, tool_helpfulness=neutral.

## Recurrent Confusion Pairs
- high -> low: 7
- high -> moderate: 5
- low -> high: 1

## Tool Use Rule
Use tool evidence for feature checking and trend/risk summaries. Do not treat tool output as gold truth.
Positive L4 episodes are successful precedents. Negative L4 episodes are failure patterns to avoid.
