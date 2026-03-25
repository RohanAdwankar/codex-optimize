# Life Example Pattern

The bundled sample in `codex-optimize/example/life` is the reference pattern.

Mapping:

- optimization target:
  `example/life/life.py`

- metric file:
  `example/life/metric.json`

- benchmark command:
  `python3 example/life/benchmark.py`

- correctness test:
  `python3 example/life/tests.py`

- agent context:
  `example/life/INFO.md`

- runtime image:
  `codopt-life:latest`, built from `example/life/Dockerfile`

Representative run command:

```bash
uv run --with fastapi --with uvicorn python main.py \
  --edit example/life/life.py \
  --metric example/life/metric.json \
  --metric-key score \
  --command "python3 example/life/benchmark.py" \
  --branch 2 \
  --time 180 \
  --info example/life/INFO.md \
  --max-agents 4 \
  --test "python3 example/life/tests.py" \
  --docker-image codopt-life:latest \
  --max-depth 3
```

Use this as the template when adapting another repo:

- your hot code path replaces `life.py`
- your benchmark replaces `benchmark.py`
- your tests replace `tests.py`
- your agent brief replaces `INFO.md`
- your Docker image replaces `codopt-life:latest`
