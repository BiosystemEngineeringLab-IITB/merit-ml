# MERIT-ML GitHub Push Automation

This file explains how to make GitHub pushes from this server non-interactive and how to publish only release-safe MERIT-ML files to the public repository.

Public repository:

```text
https://github.com/BiosystemEngineeringLab-IITB/merit-ml
```

Default local release clone:

```text
/tmp/merit-ml-release-sync
```

## Recommended Permanent Authentication: Write Deploy Key

A repository deploy key is better than repeatedly pasting a PAT because it is scoped to this one repository, can be revoked independently, and does not require interactive login.

### 1. Generate a key on this server

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 \
  -C "merit-ml-release-deploy-key-$(hostname)-$(date +%F)" \
  -f ~/.ssh/merit_ml_release_deploy \
  -N ""
cat ~/.ssh/merit_ml_release_deploy.pub
```

Do not share the private key. Only paste the `.pub` output into GitHub.

### 2. Add the public key in GitHub

In the GitHub repository page:

1. Open `BiosystemEngineeringLab-IITB/merit-ml`.
2. Go to `Settings`.
3. Go to `Deploy keys`.
4. Click `Add deploy key`.
5. Title: `MERIT-ML release server deploy key`.
6. Paste the public key from `~/.ssh/merit_ml_release_deploy.pub`.
7. Check `Allow write access`.
8. Save.

### 3. Add an SSH alias on the server

```bash
cat >> ~/.ssh/config <<'EOF_SSH'

Host github.com-merit-ml
    HostName github.com
    User git
    IdentityFile ~/.ssh/merit_ml_release_deploy
    IdentitiesOnly yes
EOF_SSH
chmod 600 ~/.ssh/config
```

Test:

```bash
ssh -T git@github.com-merit-ml
```

GitHub normally prints a message that shell access is not provided. That is OK if authentication succeeds.

### 4. Use the SSH alias as the remote

```bash
cd /tmp/merit-ml-release-sync
git remote set-url origin git@github.com-merit-ml:BiosystemEngineeringLab-IITB/merit-ml.git
git push origin main
```

After this one-time setup, future GitHub release pushes should not ask for username/password.

## Automated Release-Safe Push

Use the helper script from the main MERIT working tree:

```bash
cd /home/shayantan/metabolomics/ML-ready
./deploy/github_release_push.sh --message "Describe the MERIT-ML release change"
```

Dry run first:

```bash
./deploy/github_release_push.sh --dry-run --message "Describe the MERIT-ML release change"
```

Commit but do not push:

```bash
./deploy/github_release_push.sh --no-push --message "Describe the MERIT-ML release change"
```

Custom release clone path:

```bash
MERIT_RELEASE_REPO_DIR=/tmp/merit-ml-release-sync ./deploy/github_release_push.sh --message "Update release files"
```

Custom remote, for example HTTPS with an already configured credential helper:

```bash
MERIT_RELEASE_REMOTE=https://github.com/BiosystemEngineeringLab-IITB/merit-ml.git ./deploy/github_release_push.sh --message "Update release files"
```

## What the Script Copies

The script copies only a whitelist of release-safe files and directories needed for the public UI/Docker distribution:

- `README.md`
- `.dockerignore`
- `docker/`
- `docker-compose.merit.yml`
- `merit-ui-v2/`
- selected `deploy/` documentation, templates, and scripts

It does not copy local cache folders, raw dumps, manuscript scratch folders, private deploy configs, or token files.

## What to Do If Push Fails

If authentication fails:

1. Confirm the deploy key exists in GitHub and has write access.
2. Confirm `~/.ssh/config` contains the `github.com-merit-ml` host alias.
3. Run `ssh -T git@github.com-merit-ml`.
4. Confirm the release clone remote is `git@github.com-merit-ml:BiosystemEngineeringLab-IITB/merit-ml.git`.

If push is rejected as non-fast-forward:

```bash
cd /tmp/merit-ml-release-sync
git pull --rebase origin main
git push origin main
```

If Git reports dubious ownership:

```bash
git config --global --add safe.directory /tmp/merit-ml-release-sync
```
