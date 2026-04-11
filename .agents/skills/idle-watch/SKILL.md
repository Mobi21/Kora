---
name: idle-watch
description: Quick poll of Kora's state during idle phases — designed for /loop usage
disable-model-invocation: true
allowed-tools: Bash, Read
---

# Kora Idle Watcher

Poll Kora's current state and report changes. For use during acceptance test idle phases.

Run one poll cycle:
```bash
bash scripts/acceptance_operator/idle_watch.sh
```

Then read the full monitor snapshot if anything looks interesting:
```bash
cat /private/tmp/Codex-501/kora_monitor.json | python3 -m json.tool
```

Check anomalies:
```bash
cat /private/tmp/Codex-501/kora_anomalies.jsonl 2>/dev/null | tail -5
```

Report what you see concisely. Flag anomalies, mode changes, new work, or stuck states.
