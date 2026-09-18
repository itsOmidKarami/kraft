# Remote access

Kraft's gate needs a browser to approve or reject. Approving from a phone or
a machine that isn't the one running the server doesn't need new code —
Kraft already has password auth (`access.yaml`) and a Host allowlist for a
non-loopback bind (`allowed_hosts`).

![The board at a 390px phone viewport, with bottom tab navigation](assets/mobile.png)

Point a tunnel at it:

1. Set a password if you haven't: `kraft admin start` refuses a non-loopback
   bind without one.
2. Add the tunnel's hostname to `allowed_hosts` in `access.yaml` (Settings →
   Access, or hand-edit — see `kraft admin doctor` to confirm it parses).
3. `kraft admin start --host 0.0.0.0`.
4. Point a tunnel at the bound port:
    - **Tailscale**: `tailscale serve https / http://localhost:8765`, then
      open the board at your tailnet's HTTPS address from any device on it.
    - **Cloudflare Quick Tunnel**: `cloudflared tunnel --url http://localhost:8765`
      prints a `*.trycloudflare.com` URL — add that hostname to
      `allowed_hosts` before using it.

Signed one-shot approve/reject links and a Slack action endpoint were
considered and dropped: this gives phone access to the real board, with the
same auth, for no new code to secure. See
[Security](security.md) for the full threat model.
