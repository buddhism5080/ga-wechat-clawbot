# GA WeChat Clawbot

## Purpose
This repo is a standalone integration layer around GenericAgent. Do not modify the upstream GenericAgent repository from here.

## Constraints
- Treat GenericAgent as an upstream dependency located at a configured `ga.root` path.
- Prefer subprocess/helper-process boundaries over in-place monkeypatching of upstream files.
- Keep WeChat transport, rendering, and session logic inside this repo.
- Preserve `context_token`-scoped isolation.

## Verification
Before claiming success:
1. Run `python3 -m compileall src tests`
2. Run `python3 -m unittest discover -s tests -v`
3. If touching runner code, make sure no command writes into the upstream GA git checkout.
