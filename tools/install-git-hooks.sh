#!/bin/sh
#
# Installs this fork's git hooks and the config they read.
#
# Hooks are not tracked by git, so a fresh clone starts unprotected. Run this
# once after cloning.

set -e

repo=$(git rev-parse --show-toplevel)
cd "$repo"

cp tools/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
echo "installed .git/hooks/pre-push"

# Branches that are the head of an open upstream pull request. Pushing these
# runs CI in the upstream org and mails the maintainers.
for branch in feature/config-web-editor; do
    if ! git config --get-all fork.noPushBranches | grep -qx "$branch"; then
        git config --add fork.noPushBranches "$branch"
        echo "blocked from pushing: $branch"
    fi
done

# Belt and braces: upstream should never be a push target.
if [ "$(git remote get-url --push upstream 2>/dev/null)" != "DISABLE" ]; then
    git remote set-url --push upstream DISABLE
    echo "set upstream push url to DISABLE"
fi

echo
echo "current guards:"
echo "  upstream push url : $(git remote get-url --push upstream 2>/dev/null)"
echo "  blocked branches  : $(git config --get-all fork.noPushBranches | tr '\n' ' ')"
