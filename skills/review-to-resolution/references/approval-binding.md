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
The signature must verify against an SSH signing key that meets both conditions:
- It is published on the `APPROVAL_OWNER` GitHub account, fetched from the public `users/<login>/ssh_signing_keys` endpoint.
- It is hardware-backed (`sk-ssh-ed25519` or `sk-ecdsa`), so it needs a physical touch to sign.

The agent's credentials cannot add signing keys: the gh token has no `*:ssh_signing_key` scope. Without a touch, the agent cannot sign. A file hash alone is never accepted as evidence of who approved.

### Results
- `missing`, `legacy`, a statement that is not `approve…` (pending or refused), the wrong signer, a wrong type, target, statement or revision, a tampered signature, an unreachable key anchor, a missing signature file or a missing `ssh-keygen` all give `unverified`.
- `unverified` never satisfies a required approval while the binding is active.

## Activation boundary
- `APPROVAL_OWNER = None` in `scripts/grrp.py` means the binding is **inactive**, which is the state today. The gate applies the legacy rule and reports `approval_binding: inactive`.
- Setting the owner login means it is **active**. Every required approval in every ledger gated from then on must be `verified`.
- Compatibility consequence: once active, regating a historical ledger that holds a legacy approval on a started protected/enhancement/approval_required finding gives `invalid`. Its text is not rewritten.

## Owner setup (one time, owner only)
1. Use an SSH security key (FIDO2), such as a YubiKey, and run `ssh-keygen -t ed25519-sk -f ~/.ssh/grrp_owner_sk`.
2. In GitHub (YourBIMpossible), go to Settings, then SSH and GPG keys, then New SSH key. Choose Key type **Signing Key** and paste `grrp_owner_sk.pub`.
3. Confirm the agent cannot add or replace signing keys:
   - `gh auth status` must not list `admin:ssh_signing_key` or `write:ssh_signing_key`.
   - Never grant those scopes to the agent's token, and do not store a broader-scoped owner token in any credential store the agent can use.
4. Set `APPROVAL_OWNER = "YourBIMpossible"` in `scripts/grrp.py`. This is the activation commit.

## Per approval (owner only)
The agent prints the canonical request (`approval_request`) to a file. The owner reviews it, then signs it:

```
ssh-keygen -Y sign -f ~/.ssh/grrp_owner_sk -n grrp-approval request.txt
```

The owner touches the key. The agent records `<field>_sig: request.txt.sig` next to the ledger. The agent never signs.
