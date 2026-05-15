#!/bin/bash
set -e

echo "Building site..."
mkdocs build

# Use a temporary branch to avoid messing up the current working directory
# 1. Create a temporary branch from the current commit
TEMP_BRANCH="gh-pages-temp-$(date +%s)"
git checkout -b $TEMP_BRANCH

# 2. Remove everything and copy the built site
# We use a temporary directory to avoid deleting the .git folder
mkdir -p .tmp_site
cp -r site/* .tmp_site/
git rm -rf .
cp -r .tmp_site/* .
rm -rf .tmp_site

# 3. Commit and force push to gh-pages
git add .
git commit -m "Deploy site"
git push -f origin $TEMP_BRANCH:gh-pages

# 4. Clean up: go back to original branch and delete temp branch
git checkout -
git branch -D $TEMP_BRANCH

echo "Successfully deployed to gh-pages!"
