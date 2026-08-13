import json
from pathlib import Path

import summarize


def test_parse_nof(tmp_path: Path):
    output = tmp_path / "result.log"
    output.write_text(
        "completed_ops=10\nfailed_ops=0\nbw=1.50 GiB/s\niops=12000.00\n"
        "clat_p99=250.00 us\n"
    )
    parsed = summarize.parse_nof(output)
    assert parsed["bandwidth"] == 1.5 * 1024**3
    assert parsed["p99_ns"] == 250_000
    assert parsed["errors"] == 0


def test_parse_fio(tmp_path: Path):
    output = tmp_path / "result.json"
    output.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "error": 0,
                        "read": {
                            "io_bytes": 4096,
                            "bw_bytes": 1000,
                            "iops": 250,
                            "clat_ns": {"percentile": {"99.000000": 9000}},
                        },
                        "write": {"io_bytes": 0, "bw_bytes": 0, "iops": 0},
                    }
                ]
            }
        )
    )
    parsed = summarize.parse_fio(output)
    assert parsed == {"bandwidth": 1000.0, "iops": 250.0, "p99_ns": 9000.0, "errors": 0}
