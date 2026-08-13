# Mooncake NVMe-oF experiments

Start here. This directory is organized into three parts:

- `docs/`: experiment instructions, AI/maintainer context, and reviewed
  conclusions that should be committed to Git;
- `results/`: raw outputs, telemetry, plots, and other intermediate results;
  this directory is intentionally ignored by Git;
- the remaining files: experiment scripts and configuration templates. Normal
  users do not need to inspect them directly.

To run or maintain the experiment, read
[`docs/runbook.md`](docs/runbook.md). For the current result, read
[`docs/local-remote-decision-boundary.md`](docs/local-remote-decision-boundary.md).

Create the machine-local configuration with:

```bash
cp config.env.example config.env
```

`config.env` is also ignored by Git.
