# Configuration Notes

## Minimal config
Copy `config.example.toml` to `config.toml` and set:
- `ga.root` — path to the upstream GenericAgent checkout you actually use
- `ga.python` — Python used to run worker subprocesses

## Upstream GA requirements
The upstream checkout should have either:
- `mykey.py`, or
- `mykey.json`

configured already, because worker subprocesses instantiate GenericAgent from that checkout.

## Optional voice transcoding
If you want outbound audio to send as native WeChat voice more often, configure:

```toml
[wechat]
voice_encoder_cmd = "pilk -i {input} -o {output}"
```

If transcoding is not available or fails, the project falls back to sending the audio as a normal file.
