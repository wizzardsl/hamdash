# Spikes

Throwaway code that was written to answer a question, kept because the
answer is in it. **Nothing here is loaded by the app** — `registry.py`
reads `plugins/` and `<home>/cards/`, not this directory — so a spike
cannot turn up in somebody's Cards menu or ship in a build.

To run one, copy it into your cards folder and press **Cards → Rescan**:

```bash
cp -r spikes/remote_audio "$APPDATA/hamdash/cards/"      # Windows
cp -r spikes/remote_audio ~/.config/hamdash/cards/       # Linux
```

| | |
|---|---|
| [remote_audio](remote_audio) | can the radio's receive audio reach a LAN browser by polling, with no new dependency and no new open port? Yes — see `FINDINGS.md` |
