# Inference Optimization Through The Skill

This example captures a full `codopt` usage flow driven by the `optimize` skill. 

The target repo was a Haskell llama2 inference implementation. The skill:

- inspected the repo and identified the hot path in `Run.hs`
- created a small repo-local `.codopt/` harness with a benchmark and correctness test
- validated the setup with `codopt validate`
- ran a bounded tournament with `codopt run`
- inspected the winner and applied the winning `Run.hs` diff back to the original repo

The transcript is in [codex_optimize_inference.txt](./example/inference_optimization/codex_optimize_attempt_7.txt), and the video of the process unfolding is on the main README.MD.

Also, to make it easy to understand I uploaded the repo that codopt made to [Github](https://github.com/RohanAdwankar/optimized-llama2.hs) and then merged the high performing branch. 
