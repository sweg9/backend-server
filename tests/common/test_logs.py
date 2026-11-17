"""Stack > Logging: one JSON object per line, with the run id when there is one."""

import json
import logging

from backend.common import logs


def test_json_lines_with_context(tmp_path):
    log_file = tmp_path / "hub.log"
    logs.setup("hub", str(log_file))
    logging.getLogger("backend.hub.link").info("sent %s", "run.result", extra={"run_id": "r_1", "msg_id": "m-1"})
    line = json.loads(log_file.read_text().splitlines()[-1])
    assert line["mode"] == "hub" and line["module"] == "hub.link" and line["msg"] == "sent run.result"
    assert line["run_id"] == "r_1" and line["msg_id"] == "m-1" and line["ts"].endswith("Z")
