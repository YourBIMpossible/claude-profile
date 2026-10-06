# Approval binding (F-029)

## What is verified

### Legacy attestation (today)
A free-text value of `owner_action`, `scope_approval` or `action_approval` is a legacy attestation. It counts when it contains the word `owner` and a real date.

The gate cannot tell who wrote it. `gate --json` lists these approvals as `"status": "legacy"`, never `verified`. Historical ledgers are left as they are and are never upgraded.

### Verified approval
A verified approval has both of these:
- The free-text field, beginning `approve`, which states what is approved.
- `<field>_sig: <path relative to the ledger>`, pointing to an OpenSSH signature with namespace `grrp-approval` over the canonical request:

```
grrp-approval-v1
type=<owner_action|scope_approval|action_approval>
ledger=<ledger file name>
finding=<finding id>
statement_sha256=<sha256 of the approved statement>
revision=<sha256 of brief Objective/Scope/Boundaries/Authority + finding id/source/classification/authority/requirement/location/severity>
```

The revision is a snapshot taken before the approval. Recording `<field>_sig`, or making later progress (execution_state, evidence, obligations), does not change it. Any change to the run's scope or authority, or to the finding's identity or target, does change it, and that invalidates the approval.

### Who approved
The signature must verify against an SSH signing key that meets all of these conditions:
- It is published on the `APPROVAL_OWNER` GitHub account, fetched from the public `users/<login>/ssh_signing_keys` endpoint. The agent's gh token has no `*:ssh_signing_key` scope, and adding a key in the browser needs GitHub sudo mode (password or 2FA), so the agent cannot publish one.
- The agent cannot use it. The verifier rejects any key loaded in ssh-agent (`ssh-add -L`), and any private key under `~/.ssh` that opens without a passphrase. A software key therefore counts only if it is passphrase-protected and never added to ssh-agent: the owner types the passphrase for each signature.
- With `APPROVAL_REQUIRE_HARDWARE = True` (off by default), it must also be a hardware key (`sk-*`, one physical touch per signature).

A file hash alone is never accepted as evidence of who approved.

**Residual trust limits:**
- A software key file is readable by the agent, so the strength of its passphrase is the barrier against offline guessing.
- A passphrase typed into a session the agent can observe is exposed.
- `grrp.py` itself, including `APPROVAL_OWNER`, can be edited by the agent. Changes to it show up in the Claude-Profile git history.

### Results
- `missing`, `legacy`, a statement that is not `approve…` (pending or refused), the wrong signer, a wrong type, target, statement or revision, a tampered signature, an unreachable key anchor, a missing signature file or a missing `ssh-keygen` all give `unverified`.
- `unverified` never satisfies a required approval while the binding is active.

## Activation boundary
- `APPROVAL_OWNER = None` in `scripts/grrp.py` means the binding is **inactive**, which is the state today. The gate applies the legacy rule and reports `approval_binding: inactive`.
- Setting the owner login means it is **active**. Every required approval in every ledger gated from then on must be `verified`.
- Compatibility consequence: once active, regating a historical ledger that holds a legacy approval on a started protected/enhancement/approval_required finding gives `invalid`. Its text is not rewritten.

## Owner setup (one time, owner only; no hardware needed)
On 2026-10-06 the two existing keys in `~/.ssh` were unencrypted, so the verifier would reject both. A new key is needed.
1. In a terminal that no agent session is driving, run `ssh-keygen -t ed25519 -f ~/.ssh/grrp_owner -C grrp-owner`. Enter a strong passphrase (a long random phrase). Do not `ssh-add` this key.
2. In GitHub (YourBIMpossible), go to Settings, then SSH and GPG keys, then New SSH key. Choose Key type **Signing Key** and paste `grrp_owner.pub`. GitHub asks for sudo confirmation.
3. Confirm that `gh auth status` lists no `admin:ssh_signing_key` or `write:ssh_signing_key` scope.
4. Activate by setting `APPROVAL_OWNER = "YourBIMpossible"` in `scripts/grrp.py`. The agent may make this one-line commit once steps 1–3 are done.

**Consequences:**
- Every later protected, enhancement or approval_required execution needs one owner signature, with the passphrase typed each time.
- A historical ledger re-gated with a legacy approval on a started finding becomes `invalid`.
- If the GitHub API is unreachable, verification fails closed.

## Deactivate / roll back (no history rewrite)
- **Deactivate:** set `APPROVAL_OWNER = None` and commit. The legacy rule applies again; recorded signatures stay in place but are reported `unverified` and are not enforced.
- **Remove the feature:** `git revert <F-029 commit(s)>` on master. This keeps all later commits and the working tree as they are. Never use `reset --hard`.

## Per approval (owner only)
The agent prints the canonical request (`approval_request`) to a file. The owner reviews it, then signs it:

```
ssh-keygen -Y sign -f ~/.ssh/grrp_owner -n grrp-approval request.txt
```

The owner enters the passphrase (or touches a hardware key). The agent records `<field>_sig: request.txt.sig` next to the ledger. The agent never signs.
