# Soccer CV Dataset

Layout:

```text
assets/datasets/soccer/
  raw/                 # original multi-camera clips (gitignored)
  frames/              # extracted frames
  labels/              # CVAT / Label Studio / YOLO exports
  splits/              # train.json, val.json, test.json  (split by match)
  manifests/           # dataset and model manifests
```

Required labels:

- Camera roles: `main_wide`, `tactical_high`, `sideline_tight`, `goal_line`,
  `behind_goal_left`, `behind_goal_right`, `reaction`, `unknown`
- Objects: ball, player, referee, pitch geometry / lines
- Event phase intervals: foul, free_kick, penalty_kick, corner_kick, burst_attack
- Hard negatives: ordinary passing and resumed open play

Never enable CV auto-routing until `tools/cv/eval.py` writes a model manifest
that passes the evaluation gates in `configs/games/soccer.json`.
